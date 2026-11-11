"""The sandbox: one Jupyter kernel per run, and the notebook it writes.

Every piece of code the agent runs, including each query and each finding,
becomes a notebook cell with its outputs, saved to the run folder after every
cell. The kernel is either a local process (`LocalKernel`, the default; it gets
the run's own scoped S3 credentials when a run role is set, see credentials.py)
or runs in a separate sandbox task (`RemoteKernel`, talking to
`alchemist sandbox serve`, see sandbox_server.py), which has no route to Bedrock
and gets its S3 credentials from the broker.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path

import nbformat
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager

from .nb import RESULT_MARKER
from .paths import as_path, write_text

MAX_TEXT = 4000
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _kernel_spec_dir(module: str = "ipykernel_launcher") -> str:
    d = Path(tempfile.mkdtemp(prefix="alc-kernels-")) / "alchemist"
    d.mkdir(parents=True)
    (d / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", module, "-f", "{connection_file}"],
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


# What a run's kernel may inherit from the process that starts it. Everything else (the database URL and password,
# model API keys, the sandbox token, OIDC and plugin secrets) stays out: the kernel runs code the model wrote.
KERNEL_ENV_NAMES = frozenset({
    # the system, on Linux, macOS and Windows
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TZ", "TERM", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "ALLUSERSPROFILE", "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    # this Python
    "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONUTF8", "PYTHONIOENCODING",
    # certificates and proxies, to reach the object store
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    # what alchemist.nb and its storage backends read; the broker function (separate sandbox only)
    "ALC_S3_CREDENTIALS", "ALC_PLUGINS", "ALC_PLUGIN_MODULES", "ALC_RUN_ID", "ALC_BROKER_FUNCTION",
})
# Never inherited, though a sandbox holds them: the pseudonym key and the run's broker token are for the query step
# (querystep.py), which reads datasets; the kernel gets only its own kernel-scope token (credentials.scope_token).
# Object store access (in ambient mode the kernel uses the process's own AWS identity), locale and cache settings.
KERNEL_ENV_PREFIXES = ("AWS_", "LC_", "CLOUDPATHLIB_")
# Never, even with a prefix above: a Bedrock API key is for model calls, which the kernel does not make.
KERNEL_ENV_NEVER = frozenset({"AWS_BEARER_TOKEN_BEDROCK", "ALC_PSEUDONYM_KEY", "ALC_BROKER_TOKEN"})


def kernel_env_allowed(name: str, extra: tuple[str, ...] = ()) -> bool:
    """Whether a run's kernel may inherit this variable. `extra`: names, or prefixes ending in `*`, an operator
    allowed with ALC_KERNEL_ENV (a plugin's storage setting, say); never one of KERNEL_ENV_NEVER."""
    key = name.upper()  # Windows names are case-insensitive
    if key in KERNEL_ENV_NEVER:
        return False
    if key in KERNEL_ENV_NAMES or key.startswith(KERNEL_ENV_PREFIXES):
        return True
    return any(key.startswith(e[:-1].upper()) if e.endswith("*") else key == e.upper() for e in extra)


def kernel_base_env(base: dict) -> dict[str, str]:
    """The variables of `base` a run's kernel inherits (see KERNEL_ENV_NAMES)."""
    extra = tuple(e.strip() for e in (base.get("ALC_KERNEL_ENV") or "").split(",") if e.strip())
    return {k: v for k, v in base.items() if kernel_env_allowed(k, extra)}


def kernel_env(run_dir: str, extra_env: dict | None = None, base: dict | None = None) -> dict[str, str]:
    """Environment of a run's kernel: the allowed part of `base` (this process's environment), the run's settings,
    and `extra_env`; None in extra_env removes a variable."""
    env = {**kernel_base_env(os.environ if base is None else base), "ALC_RUN_DIR": str(run_dir),
           "MPLBACKEND": "module://matplotlib_inline.backend_inline", "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1",
           "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", **(extra_env or {})}
    return {k: v for k, v in env.items() if v is not None}


def kernel_jail(run_dir: str, read_paths: list[str] | None, extra_read: list[str] | None = None, *,
                mode: str = "auto") -> dict | None:
    """The paths a run's kernel may use ({"read": [...], "write": [...]}), or None to leave it unrestricted.

    mode (ALC_KERNEL_JAIL): auto confines the kernel where Linux Landlock is available (fsjail.py) and leaves it
    unrestricted elsewhere (Windows, macOS, a container runtime that blocks Landlock); required refuses to start a
    kernel without it; off never confines it. `read_paths`: local folders besides its run folder the kernel may read
    (none for a run's kernel: its queries run in the query step, see querystep.py); it writes only its run folder."""
    from . import fsjail

    if mode not in ("auto", "required", "off"):
        raise ValueError(f"ALC_KERNEL_JAIL is {mode!r}; use auto, required or off")
    if mode == "off":
        return None
    if not fsjail.available():
        if mode == "required":
            raise PermissionError("ALC_KERNEL_JAIL=required, but this machine has no Landlock (Linux 5.13+)")
        return None
    local = [p for p in (read_paths or []) if "://" not in p]
    run = [] if "://" in str(run_dir) else [str(run_dir)]
    return {"read": [*local, *(extra_read or []), *run], "write": run}


def kernel_transport(platform: str = sys.platform) -> dict[str, str]:
    """How this process talks to its kernel: Unix sockets in a private folder, so other local users cannot connect.

    ZeroMQ has no IPC transport on Windows, so there it falls back to TCP on localhost (fine for a local demo)."""
    if platform == "win32":
        return {"transport": "tcp", "ip": "127.0.0.1"}
    return {"transport": "ipc", "ip": str(Path(tempfile.mkdtemp(prefix="alc-ipc-")) / "kernel")}


class LocalKernel:
    """A Jupyter kernel in a child process of this one.

    With `jail` ({"read": [...], "write": [...]}) the kernel restricts itself to those paths with Landlock
    (fsjail.py) before it runs anything, and keeps its connection file, sockets and temporary files in a private
    folder, so no other kernel can read its key or connect to it.
    """

    def __init__(self, env: dict[str, str], *, jail: dict | None = None):
        self.env = env
        self.jail = jail
        self.private: Path | None = None
        self.km: KernelManager | None = None
        self.kc = None
        self.credentials: dict | None = None

    def _jailed_env(self) -> tuple[dict, dict]:
        self.private = Path(tempfile.mkdtemp(prefix="alc-kernel-"))
        priv = str(self.private)
        env = {**self.env, "TMPDIR": priv, "MPLCONFIGDIR": priv, "IPYTHONDIR": priv, "JUPYTER_RUNTIME_DIR": priv,
               "ALC_JAIL_READ": os.pathsep.join(self.jail.get("read", [])),
               "ALC_JAIL_WRITE": os.pathsep.join([priv, *self.jail.get("write", [])])}
        return env, {"transport": "ipc", "ip": str(self.private / "kernel"),
                     "connection_file": str(self.private / "connection.json")}

    def start(self) -> None:
        from . import fsjail

        # The kernel runs as this process's user: keep it from reading this process's environment and memory.
        fsjail.protect_process()
        env, transport = self.env, kernel_transport()
        if self.jail is not None:
            env, transport = self._jailed_env()
        ksm = KernelSpecManager(kernel_dirs=[_kernel_spec_dir("alchemist.fsjail" if self.jail is not None
                                                              else "ipykernel_launcher")])
        self.km = KernelManager(kernel_name="alchemist", kernel_spec_manager=ksm, **transport)
        extra = []
        if transport["transport"] == "tcp":
            # Windows only: localhost TCP is expected there, so drop ipykernel's "running over TCP without
            # encryption" notice, and pyzmq's note that it adds a selector thread to the Proactor event loop.
            extra = ["--IPKernelApp.log_level=ERROR"]
            warnings.filterwarnings("ignore", message="Proactor event loop does not implement add_reader",
                                    category=RuntimeWarning)
        self.km.start_kernel(env=env, extra_arguments=extra, **({"cwd": str(self.private)} if self.private else {}))
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)

    def stop(self) -> None:
        if self.kc:
            self.kc.stop_channels()
        if self.km:
            self.km.shutdown_kernel(now=True)
        if self.private:
            import shutil

            shutil.rmtree(self.private, ignore_errors=True)

    def execute_interactive(self, code: str, *, output_hook, timeout: float) -> dict:
        return self.kc.execute_interactive(code, output_hook=output_hook, timeout=timeout, allow_stdin=False)


class SandboxUnavailable(RuntimeError):
    pass


class RemoteKernel:
    """The kernel of a separate sandbox task, over `alchemist sandbox serve`'s HTTP API.

    The sandbox builds the kernel's environment itself (its broker credentials); this side only says which run
    folder, mode and seed to use. Queries go to the sandbox's query step (POST /query), never to the kernel.
    """

    def __init__(self, url: str, *, run_dir: str, run_mode: str, seed: int, ready_timeout: float = 600,
                 token: str | None = None, read_paths: list[str] | None = None):
        self.url = url.rstrip("/")
        self.run_dir = str(run_dir)
        self.start_body = {"run_dir": self.run_dir, "run_mode": run_mode, "seed": seed}
        if read_paths is not None:
            self.start_body["read_paths"] = list(read_paths)
        self.pushed_record: dict | None = None
        self.ready_timeout = ready_timeout
        # A sandbox service shared by several runs wants the token it was started with (ALC_SANDBOX_TOKEN).
        self.token = token if token is not None else os.environ.get("ALC_SANDBOX_TOKEN")
        self.credentials: dict | None = None

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:2000]
            raise SandboxUnavailable(f"sandbox {method} {path} failed: HTTP {e.code}: {detail}") from None

    def wait_ready(self) -> None:
        deadline, last = time.monotonic() + self.ready_timeout, None
        while time.monotonic() < deadline:
            try:
                if self._call("GET", "/healthz", timeout=5).get("ok"):
                    return
            except (OSError, SandboxUnavailable) as e:
                last = e
            time.sleep(2)
        raise SandboxUnavailable(f"sandbox at {self.url} did not become ready: {last}")

    def push_first(self, credentials: dict, record: dict, query_credentials: dict | None = None) -> None:
        """Start the kernel with these credentials (credential_process JSON) and its query step with
        `query_credentials`; the manifest keeps `record`."""
        self.start_body["credentials"] = credentials
        if query_credentials is not None:
            self.start_body["query_credentials"] = query_credentials
        self.pushed_record = record

    def push_credentials(self, credentials: dict, scope: str = "kernel") -> None:
        self._call("POST", "/credentials", {"run_dir": self.run_dir, "credentials": credentials, "scope": scope},
                   timeout=30)

    def query(self, *, views: dict, qid: str, sql: str | None, mode: str, timeout: float = 900) -> dict:
        """Run one query in the sandbox's query step (querystep.py); raises QueryFailed with the step's message."""
        from .querystep import QueryFailed

        out = self._call("POST", "/query", {"run_dir": self.run_dir, "views": views, "qid": qid, "sql": sql,
                                            "mode": mode, "timeout": timeout}, timeout=timeout + 60)
        if "error" in out:
            raise QueryFailed(out["error"])
        return out

    def start(self) -> None:
        self.wait_ready()
        record = self._call("POST", "/start", self.start_body, timeout=120).get("credentials") or {}
        if self.pushed_record is not None:
            record = {**self.pushed_record, **{k: v for k, v in record.items() if k == "filesystem"}}
        self.credentials = record

    def stop(self) -> None:
        try:
            self._call("POST", "/stop", {"run_dir": self.run_dir}, timeout=30)
        except (OSError, SandboxUnavailable):
            pass  # the workflow stops the task anyway

    def execute_interactive(self, code: str, *, output_hook, timeout: float) -> dict:
        out = self._call("POST", "/execute", {"run_dir": self.run_dir, "code": code, "timeout": timeout},
                         timeout=timeout + 60)
        for msg in out["messages"]:
            output_hook(msg)
        return {"content": out["reply"]}


class Sandbox:
    def __init__(self, run_dir: str, *, run_mode: str = "live", seed: int = 1729, extra_env: dict | None = None,
                 kernel=None, query_step=None):
        """`query_step(qid=, sql=, mode=)` runs a query outside the kernel (querystep.py) and returns its record."""
        self.run = as_path(run_dir)
        self.query_step = query_step
        self.run_mode = run_mode
        self.seed = seed
        self.kernel = kernel or LocalKernel(kernel_env(str(self.run), extra_env))
        self.nb = nbformat.v4.new_notebook()
        self.nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
        self.nb.metadata["alchemist"] = {"run_mode": run_mode}
        self.counter = 0
        self.on_stop: list = []  # e.g. the credential refresher of an in-process kernel

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.kernel.start()

    def stop(self) -> None:
        try:
            self.kernel.stop()
        finally:
            while self.on_stop:
                self.on_stop.pop()()

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

    # ---------- queries ----------
    def run_query(self, qid: str, sql: str | None, *, mode: str | None = None) -> dict:
        """Run a query in the query step (outside the kernel), which stores its result in the run folder; the
        query cell then loads it. Raises querystep.QueryFailed."""
        from .querystep import QueryFailed

        if self.query_step is None:
            raise QueryFailed("this run reads no datasets (a replay reads only its snapshots)")
        return self.query_step(qid=qid, sql=sql, mode=mode or self.run_mode)

    def query(self, qid: str, sql: str, *, meta: dict | None = None) -> CellResult:
        """A query cell: the query step runs the SQL, then the kernel loads its result as df_<qid>. A rejected or
        failing query adds no cell; the error is returned as the cell's."""
        from .querystep import QueryFailed

        try:
            self.run_query(qid, sql)
        except QueryFailed as e:
            res = CellResult()
            res.error, res.cell_id = str(e), None
            return res
        return self.execute(query_cell(qid, sql), kind="query", meta={"query_id": qid, **(meta or {})})

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

        reply = self.kernel.execute_interactive(code, output_hook=hook, timeout=timeout)
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

    def restore(self, *, cells: int, counter: int) -> list[dict]:
        """Rebuild the kernel of a paused (or interrupted) run in this fresh kernel.

        The notebook is read back up to the checkpoint (`cells` cells, numbered up to `counter`); its code cells are
        re-run in replay mode, so each query reads the snapshot it stored and nothing reads the lake again; then the
        kernel switches back to this sandbox's mode. Nothing is added to the notebook. Returns the cells that raised
        (cells that failed the first time fail again; that is expected)."""
        self.nb = nbformat.reads((self.run / "notebook.ipynb").read_text(), as_version=4)
        self.nb.cells = self.nb.cells[:cells]
        self.counter = counter

        def quiet(code: str) -> str | None:
            err: list[str] = []

            def hook(msg):
                if msg["msg_type"] == "error":
                    err.append(f"{msg['content']['ename']}: {msg['content']['evalue']}")
            self.kernel.execute_interactive(code, output_hook=hook, timeout=900)
            return err[0] if err else None

        failed = quiet(f"run_mode = 'replay'\nseed = {self.seed}\nimport os\nimport alchemist.nb as alc\n\n"
                       "alc.init(os.environ['ALC_RUN_DIR'], run_mode=run_mode, seed=seed)")
        if failed:
            raise RuntimeError(f"sandbox setup failed while resuming: {failed}")
        errors = []
        for c in self.nb.cells:
            meta = c.metadata.get("alchemist", {})
            if c.cell_type != "code" or meta.get("kind") in ("parameters", "setup"):
                continue
            failed = quiet(c.source)
            if failed:
                errors.append({"cell": meta.get("cell_id"), "error": failed[:500]})
        failed = quiet(f"run_mode = {self.run_mode!r}\nalc.resume(run_mode)")
        if failed:
            raise RuntimeError(f"could not switch the resumed kernel back to {self.run_mode}: {failed}")
        self.save()
        return errors

    def add_markdown(self, text: str) -> None:
        self.nb.cells.append(nbformat.v4.new_markdown_cell(text))
        self.save()

    def save(self) -> None:
        write_text(self.run / "notebook.ipynb", nbformat.writes(self.nb))


def query_cell(qid: str, sql: str) -> str:
    """The cell that loads a query's result; the SQL documents it (the query step ran it, outside the kernel)."""
    return f'df_{qid} = alc.query({qid!r}, """\n{sql.strip()}\n""")\ndf_{qid}.head()'


def finding_cell(fid: str, *, statement: str, value_expr: str, unit: str, verdict: str, confidence: str,
                 hypothesis: str, evidence: list[str]) -> str:
    return (f"alc.finding(\n    {fid!r},\n    hypothesis={hypothesis!r},\n    statement={statement!r},\n"
            f"    value={value_expr},\n    unit={unit!r},\n    verdict={verdict!r},\n    confidence={confidence!r},\n"
            f"    evidence={evidence!r},\n);")
