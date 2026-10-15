"""The sandbox: one Jupyter kernel per run, and the notebook it writes.

Every piece of code the agent runs, including each query and each finding,
becomes a notebook cell with its outputs, saved to the run folder after every
cell. The kernel gets the run's own scoped S3 credentials when a run role is set
(credentials.py). In production it also runs in its own container with no
network route to Bedrock; in the MVP it is a local process.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import nbformat
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager

from .nb import RESULT_MARKER
from .paths import as_path, write_text

MAX_TEXT = 4000
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _kernel_spec_dir() -> str:
    d = Path(tempfile.mkdtemp(prefix="alc-kernels-")) / "alchemist"
    d.mkdir(parents=True)
    (d / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "Alchemist (Python)", "language": "python"}))
    return str(d.parent)


class CellResult:
    def __init__(self):
        self.outputs: list = []
        self.text: list[str] = []
        self.results: list[dict] = []
        self.images: list[bytes] = []
        self.figure_paths: list[str] = []
        self.error: str | None = None

    def model_text(self) -> str:
        text = "".join(self.text)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + f"\n... [{len(text) - MAX_TEXT} more characters truncated]"
        if self.error:
            text += f"\nERROR: {self.error}"
        return text.strip()


class Sandbox:
    def __init__(self, run_dir: str, *, run_mode: str = "live", seed: int = 1729, extra_env: dict | None = None):
        self.run = as_path(run_dir)
        self.run_mode = run_mode
        self.seed = seed
        env = {**os.environ, "ALC_RUN_DIR": str(self.run), "MPLBACKEND": "module://matplotlib_inline.backend_inline",
               "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
               **(extra_env or {})}
        self.env = {k: v for k, v in env.items() if v is not None}  # None in extra_env removes a variable
        self.nb = nbformat.v4.new_notebook()
        self.nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
        self.nb.metadata["alchemist"] = {"run_mode": run_mode}
        self.km: KernelManager | None = None
        self.kc = None
        self.counter = 0

    # ---------- lifecycle ----------
    def start(self) -> None:
        ksm = KernelSpecManager(kernel_dirs=[_kernel_spec_dir()])
        self.km = KernelManager(kernel_name="alchemist", kernel_spec_manager=ksm, transport="ipc",
                                ip=str(Path(tempfile.mkdtemp(prefix="alc-ipc-")) / "kernel"))
        self.km.start_kernel(env=self.env)
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)

    def stop(self) -> None:
        if self.kc:
            self.kc.stop_channels()
        if self.km:
            self.km.shutdown_kernel(now=True)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ---------- standard cells ----------
    def setup(self) -> CellResult:
        params = f"# Parameters: change run_mode to 'replay' or 'rerun' to reproduce this run\nrun_mode = {self.run_mode!r}\nseed = {self.seed}"
        self.execute(params, kind="parameters", tags=["parameters"])
        res = self.execute("import os\nimport alchemist.nb as alc\n\nalc.init(os.environ['ALC_RUN_DIR'], run_mode=run_mode, seed=seed)",
                           kind="setup")
        if res.error:
            raise RuntimeError(f"sandbox setup failed: {res.error}")
        return res

    # ---------- execution ----------
    def execute(self, code: str, *, kind: str = "python", tags: list[str] | None = None,
                meta: dict | None = None, timeout: float = 900) -> CellResult:
        self.counter += 1
        cell_id = f"c{self.counter}"
        res = CellResult()

        def hook(msg):
            t, c = msg["msg_type"], msg["content"]
            if t == "stream":
                res.outputs.append(nbformat.v4.new_output("stream", name=c["name"], text=c["text"]))
                for line in c["text"].splitlines(keepends=True):
                    if line.startswith(RESULT_MARKER):
                        try:
                            res.results.append(json.loads(line[len(RESULT_MARKER):]))
                        except json.JSONDecodeError:
                            pass
                        continue
                    res.text.append(line)
            elif t in ("execute_result", "display_data"):
                data = c.get("data", {})
                if t == "execute_result":
                    res.outputs.append(nbformat.v4.new_output("execute_result", data=data, execution_count=self.counter))
                else:
                    res.outputs.append(nbformat.v4.new_output("display_data", data=data, metadata=c.get("metadata", {})))
                if "image/png" in data:
                    png = base64.b64decode(data["image/png"])
                    res.images.append(png)
                    rel = f"figures/{cell_id}_{len(res.images)}.png"
                    target = self.run / rel
                    if not hasattr(target, "cloud_prefix"):
                        target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(png)
                    res.figure_paths.append(rel)
                    res.text.append(f"[figure saved: {rel}]\n")
                elif "text/plain" in data:
                    res.text.append(data["text/plain"] + "\n")
            elif t == "error":
                tb = ANSI.sub("", "\n".join(c.get("traceback", [])[-3:]))
                res.error = f"{c['ename']}: {c['evalue']}\n{tb}"[:2000]
                res.outputs.append(nbformat.v4.new_output("error", ename=c["ename"], evalue=c["evalue"],
                                                          traceback=c.get("traceback", [])))

        reply = self.kc.execute_interactive(code, output_hook=hook, timeout=timeout, allow_stdin=False)
        if reply["content"].get("status") == "error" and res.error is None:
            res.error = f"{reply['content'].get('ename')}: {reply['content'].get('evalue')}"

        cell = nbformat.v4.new_code_cell(code, execution_count=self.counter, outputs=res.outputs)
        cell.metadata["alchemist"] = {"cell_id": cell_id, "kind": kind, **(meta or {})}
        if tags:
            cell.metadata["tags"] = tags
        self.nb.cells.append(cell)
        self.save()
        res.cell_id = cell_id
        return res

    def add_markdown(self, text: str) -> None:
        self.nb.cells.append(nbformat.v4.new_markdown_cell(text))
        self.save()

    def save(self) -> None:
        write_text(self.run / "notebook.ipynb", nbformat.writes(self.nb))


def query_cell(qid: str, sql: str) -> str:
    return f'df_{qid} = alc.query({qid!r}, """\n{sql.strip()}\n""")\ndf_{qid}.head()'


def finding_cell(fid: str, *, statement: str, value_expr: str, unit: str, verdict: str, confidence: str,
                 hypothesis: str, evidence: list[str]) -> str:
    return (f"alc.finding(\n    {fid!r},\n    hypothesis={hypothesis!r},\n    statement={statement!r},\n"
            f"    value={value_expr},\n    unit={unit!r},\n    verdict={verdict!r},\n    confidence={confidence!r},\n"
            f"    evidence={evidence!r},\n);")
