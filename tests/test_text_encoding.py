"""Alchemist reads and writes its own text files as UTF-8 on every platform.

Without an explicit encoding, Python uses the locale's code page, which on Windows is often cp1252: a degree sign,
a "≥" or a Greek letter in a report, a plan or a knowledge file then fails with "'charmap' codec can't encode
character". This test keeps every text read and write in src/alchemist explicit.
"""

from __future__ import annotations

import ast
from pathlib import Path

from alchemist.sandbox import kernel_env

SRC = Path(__file__).resolve().parents[1] / "src" / "alchemist"


def _text_mode(call: ast.Call, mode_pos: int) -> bool:
    mode = next((k.value for k in call.keywords if k.arg == "mode"), None)
    if mode is None and len(call.args) > mode_pos:
        mode = call.args[mode_pos]
    if mode is None:
        return True
    return not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value)


def _missing_encoding(call: ast.Call) -> bool:
    if any(k.arg == "encoding" for k in call.keywords) or any(k.arg is None for k in call.keywords):
        return False
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    if name in ("read_text", "write_text"):  # Path and CloudPath methods; paths.write_text is UTF-8 itself
        return isinstance(f, ast.Attribute)
    if name == "open" and isinstance(f, ast.Name):  # builtin open(path, mode)
        return _text_mode(call, 1)
    if name == "open" and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") != "os":  # Path.open(mode)
        return _text_mode(call, 0)
    if name == "fdopen":
        return _text_mode(call, 1)
    if name in ("run", "Popen", "check_output") and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "subprocess":
        return any(k.arg in ("text", "universal_newlines") and getattr(k.value, "value", False) for k in call.keywords)
    return False


def test_every_text_read_and_write_names_its_encoding():
    offenders = []
    for py in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _missing_encoding(node):
                offenders.append(f"{py.relative_to(SRC.parent)}:{node.lineno}")
    assert not offenders, "pass encoding='utf-8' at:\n" + "\n".join(offenders)


def test_kernel_runs_in_utf8_mode_whatever_the_parent_says():
    env = kernel_env("/tmp/run", base={"PATH": "/bin", "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_query_step_runs_in_utf8_mode(monkeypatch, tmp_path):
    import subprocess

    from alchemist import querystep

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"rows": 0}', stderr="")

    monkeypatch.setattr(querystep.subprocess, "run", fake_run)
    runner = querystep.QueryRunner({"PATH": "/bin", "PYTHONUTF8": "0"}, key="k", jail_mode="off")
    runner.run(run_dir=str(tmp_path), views={}, qid="q1", sql="select 1")
    assert seen["env"]["PYTHONUTF8"] == "1" and seen["encoding"] == "utf-8"


def test_windows_output_is_switched_to_utf8():
    import io

    from alchemist.cli import utf8_output

    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    utf8_output("linux", [out])
    assert out.encoding == "cp1252"
    utf8_output("win32", [out])
    out.write("melt ≥ 230 °C, µ = 1.2\n")
    out.flush()
    assert out.buffer.getvalue().decode("utf-8") == "melt ≥ 230 °C, µ = 1.2\n"
