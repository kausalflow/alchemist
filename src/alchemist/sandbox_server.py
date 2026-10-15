"""`alchemist sandbox serve`: the analysis kernel in its own task (workflow.sandbox_mode = separate).

The sandbox task has no route to Bedrock and its role may only invoke the credential broker. The orchestrator
(`worker analyse --sandbox-task-arn ...`) drives the kernel over this small HTTP API, reachable only from the
orchestrator's security group:

    GET  /healthz    {"ok": true, "kernel": <running>}
    POST /start      {"run_dir", "run_mode", "seed"} -> {"credentials": <manifest record>}
    POST /execute    {"code", "timeout"} -> {"messages": [{"msg_type", "content"}], "reply": <execute_reply content>}
    POST /stop       {}

The kernel's environment is built here, not by the caller: this task's own variables (the pseudonym key, plugin
settings) and, when ALC_BROKER_FUNCTION is set, an AWS profile whose credential_process is
`alchemist broker credentials`, so S3 access is whatever the broker grants this run and is refreshed as it expires.
Without ALC_BROKER_FUNCTION (a laptop) the kernel uses the ambient credentials.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .credentials import process_profile_env
from .sandbox import LocalKernel, kernel_env

MAX_BODY = 16 * 1024 * 1024


class KernelHost:
    """One kernel at a time, for the one run this task serves."""

    def __init__(self, env: dict | None = None):
        self.env = dict(os.environ if env is None else env)
        self.kernel: LocalKernel | None = None
        self.lock = threading.Lock()

    def _check_run_dir(self, run_dir: str) -> None:
        run_id = self.env.get("ALC_RUN_ID")
        if run_id and str(run_dir).rstrip("/").rsplit("/", 1)[-1] != run_id:
            raise PermissionError(f"this sandbox serves run {run_id}, not {run_dir}")

    def credentials_env(self) -> tuple[dict, dict]:
        if not self.env.get("ALC_BROKER_FUNCTION"):
            return {}, {"mode": "ambient"}
        command = [sys.executable, "-m", "alchemist.cli", "broker", "credentials"]
        extra = process_profile_env(tempfile.mkdtemp(prefix="alc-aws-"), command)
        return extra, {"mode": "broker", "function": self.env["ALC_BROKER_FUNCTION"]}

    def start(self, run_dir: str, run_mode: str = "live", seed: int = 1729) -> dict:
        with self.lock:
            self._check_run_dir(run_dir)
            if self.kernel is not None:
                self.kernel.stop()
            extra, record = self.credentials_env()
            # The kernel never needs these, and the orchestrator's settings have no business in it.
            extra.update({k: None for k in self.env if k.startswith(("ALC_DB", "ALC_LEAD", "ALC_SUB", "ALC_BEDROCK"))})
            self.kernel = LocalKernel(kernel_env(run_dir, extra, base=self.env))
            self.kernel.start()
            return {"credentials": record}

    def execute(self, code: str, timeout: float = 900) -> dict:
        with self.lock:
            if self.kernel is None:
                raise RuntimeError("no kernel; POST /start first")
            messages = []
            reply = self.kernel.execute_interactive(
                code, output_hook=lambda m: messages.append({"msg_type": m["msg_type"], "content": m["content"]}),
                timeout=timeout)
            return {"messages": messages, "reply": reply["content"]}

    def stop(self) -> None:
        with self.lock:
            if self.kernel is not None:
                self.kernel.stop()
                self.kernel = None


def make_handler(host: KernelHost):
    class Handler(BaseHTTPRequestHandler):
        server_version = "alchemist-sandbox"

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 - http.server naming
            if self.path == "/healthz":
                self._send(200, {"ok": True, "kernel": host.kernel is not None})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/start":
                    out = host.start(body["run_dir"], body.get("run_mode", "live"), int(body.get("seed", 1729)))
                elif self.path == "/execute":
                    out = host.execute(body["code"], float(body.get("timeout", 900)))
                elif self.path == "/stop":
                    host.stop()
                    out = {"ok": True}
                else:
                    return self._send(404, {"error": "not found"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except TimeoutError as e:
                return self._send(504, {"error": f"cell timed out: {e}"})
            except (KeyError, ValueError, RuntimeError) as e:
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
            self._send(200, out)

        def log_message(self, fmt, *args):  # one line per request on stderr, no bodies
            sys.stderr.write("sandbox %s\n" % (fmt % args))

    return Handler


def make_server(host: str = "0.0.0.0", port: int = 8888, kernel_host: KernelHost | None = None) -> ThreadingHTTPServer:
    kh = kernel_host or KernelHost()
    server = ThreadingHTTPServer((host, port), make_handler(kh))
    server.kernel_host = kh
    return server


def serve(host: str = "0.0.0.0", port: int = 8888) -> None:
    server = make_server(host, port)
    print(f"sandbox kernel API on {host}:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    finally:
        server.kernel_host.stop()
