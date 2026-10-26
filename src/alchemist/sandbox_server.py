"""`alchemist sandbox serve`: the analysis kernels in their own service.

Two deployments use it:

- AWS, workflow.sandbox_mode = separate: one sandbox task per run (ALC_RUN_ID), with no route to Bedrock and a role
  that may only invoke the credential broker. The orchestrator (`worker analyse --sandbox-task-arn ...`) drives it.
- docker compose and the Helm chart: one sandbox service the API drives for every run (ALC_SANDBOX_URL), with no
  database, model or PI credentials, and no network but the object store; it answers only with ALC_SANDBOX_TOKEN.

The API, reachable only from the orchestrator or the API:

    GET  /healthz    {"ok": true, "kernel": <any running>, "kernels": <number running>}
    POST /start      {"run_dir", "run_mode", "seed"} -> {"credentials": <manifest record>}
    POST /execute    {"run_dir", "code", "timeout"} -> {"messages": [{"msg_type", "content"}], "reply": <execute_reply content>}
    POST /stop       {"run_dir"}

One kernel per run folder. `run_dir` may be left out of /execute and /stop while one kernel runs (the one-run task).
The kernel's environment is built here, not by the caller: this process's own variables (the pseudonym key, plugin
settings, storage credentials) and, when ALC_BROKER_FUNCTION is set, an AWS profile whose credential_process is
`alchemist broker credentials`, so S3 access is whatever the broker grants this run and is refreshed as it expires.
Without ALC_BROKER_FUNCTION the kernel uses the ambient credentials.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .credentials import process_profile_env
from .sandbox import LocalKernel, kernel_env

MAX_BODY = 16 * 1024 * 1024
# Variables the kernel never gets: the orchestrator's settings, and the token that drives this service.
KERNEL_DROPS = ("ALC_DB", "ALC_LEAD", "ALC_SUB", "ALC_BEDROCK", "ALC_SANDBOX_TOKEN")


class Busy(RuntimeError):
    pass


class KernelHost:
    """The kernels of the runs this service serves, one per run folder."""

    def __init__(self, env: dict | None = None, *, max_kernels: int | None = None, idle_timeout: float | None = None):
        self.env = dict(os.environ if env is None else env)
        self.max_kernels = max_kernels or int(self.env.get("ALC_SANDBOX_MAX_KERNELS", "4"))
        # A kernel nobody used for this long is stopped when another run needs room (its worker died).
        self.idle_timeout = idle_timeout or float(self.env.get("ALC_SANDBOX_IDLE_SECONDS", str(4 * 3600)))
        self.kernels: dict[str, LocalKernel] = {}
        self.used: dict[str, float] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()

    @property
    def kernel(self) -> LocalKernel | None:
        return next(iter(self.kernels.values()), None)

    def _check_run_dir(self, run_dir: str) -> str:
        run_dir = str(run_dir).rstrip("/")
        run_id = self.env.get("ALC_RUN_ID")
        if run_id and run_dir.rsplit("/", 1)[-1] != run_id:
            raise PermissionError(f"this sandbox serves run {run_id}, not {run_dir}")
        root = (self.env.get("ALC_WORKSPACE") or "").rstrip("/")
        if root and (not run_dir.startswith(root + "/") or "/../" in run_dir + "/"):
            raise PermissionError(f"{run_dir} is not a run folder in this sandbox's workspace")
        return run_dir

    def credentials_env(self) -> tuple[dict, dict]:
        if not self.env.get("ALC_BROKER_FUNCTION"):
            return {}, {"mode": "ambient"}
        command = [sys.executable, "-m", "alchemist.cli", "broker", "credentials"]
        extra = process_profile_env(tempfile.mkdtemp(prefix="alc-aws-"), command)
        return extra, {"mode": "broker", "function": self.env["ALC_BROKER_FUNCTION"]}

    def _key(self, run_dir: str | None) -> str:
        with self.lock:
            if run_dir is None:
                if len(self.kernels) != 1:
                    raise RuntimeError("no kernel; POST /start first" if not self.kernels else "say which run_dir")
                return next(iter(self.kernels))
            key = str(run_dir).rstrip("/")
            if key not in self.kernels:
                raise RuntimeError(f"no kernel for {key}; POST /start first")
            return key

    def _make_room(self) -> None:
        now = time.monotonic()
        for key in [k for k, t in self.used.items() if now - t > self.idle_timeout]:
            self.kernels.pop(key).stop()
            self.used.pop(key, None)
        if len(self.kernels) >= self.max_kernels:
            raise Busy(f"{len(self.kernels)} kernels are running, the most this sandbox allows (ALC_SANDBOX_MAX_KERNELS)")

    def start(self, run_dir: str, run_mode: str = "live", seed: int = 1729) -> dict:
        key = self._check_run_dir(run_dir)
        with self.lock:
            old = self.kernels.pop(key, None)
            if old is not None:
                old.stop()
            self._make_room()
            extra, record = self.credentials_env()
            extra.update({k: None for k in self.env if k.startswith(KERNEL_DROPS)})
            kernel = LocalKernel(kernel_env(key, extra, base=self.env))
            kernel.start()
            self.kernels[key], self.used[key] = kernel, time.monotonic()
            self.locks.setdefault(key, threading.Lock())
            return {"credentials": record}

    def execute(self, code: str, timeout: float = 900, run_dir: str | None = None) -> dict:
        key = self._key(run_dir)
        with self.locks[key]:
            kernel = self.kernels.get(key)
            if kernel is None:
                raise RuntimeError(f"no kernel for {key}; POST /start first")
            self.used[key] = time.monotonic()
            messages = []
            reply = kernel.execute_interactive(
                code, output_hook=lambda m: messages.append({"msg_type": m["msg_type"], "content": m["content"]}),
                timeout=timeout)
            self.used[key] = time.monotonic()
            return {"messages": messages, "reply": reply["content"]}

    def stop(self, run_dir: str | None = None) -> None:
        with self.lock:
            keys = list(self.kernels) if run_dir is None else [str(run_dir).rstrip("/")]
            for key in keys:
                kernel = self.kernels.pop(key, None)
                self.used.pop(key, None)
                if kernel is not None:
                    kernel.stop()


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

        def _authorised(self) -> bool:
            token = host.env.get("ALC_SANDBOX_TOKEN")
            given = self.headers.get("Authorization", "")
            return not token or hmac.compare_digest(given.encode(), f"Bearer {token}".encode())

        def do_GET(self):  # noqa: N802 - http.server naming
            if self.path == "/healthz":
                self._send(200, {"ok": True, "kernel": bool(host.kernels), "kernels": len(host.kernels)})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._authorised():
                return self._send(401, {"error": "missing or wrong sandbox token"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/start":
                    out = host.start(body["run_dir"], body.get("run_mode", "live"), int(body.get("seed", 1729)))
                elif self.path == "/execute":
                    out = host.execute(body["code"], float(body.get("timeout", 900)), body.get("run_dir"))
                elif self.path == "/stop":
                    host.stop(body.get("run_dir"))
                    out = {"ok": True}
                else:
                    return self._send(404, {"error": "not found"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except Busy as e:
                return self._send(429, {"error": str(e)})
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
