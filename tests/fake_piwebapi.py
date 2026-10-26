# Copied from penguineer (tests/fake_piwebapi.py, Apache-2.0) to test Alchemist's PI source end to end.
"""A small in-process PI Web API: the GET endpoints ``fetch pi`` uses, over real HTTP.

Values follow the PI Web API's JSON: ``Items`` of ``Timestamp``, ``Value`` (a number, or a
digital state object), ``Good``, ``Questionable`` and ``Substituted``. Recorded calls honour
``startTime``, ``endTime`` and ``maxCount`` the way the server does, so paging is exercised.
"""

from __future__ import annotations

import base64
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

T0 = datetime(2026, 10, 1, tzinfo=UTC)
USER, PASSWORD = "reader", "s3cret"


def _ts(t: datetime) -> str:
    # The PI Web API writes up to seven fractional digits.
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _series() -> dict[str, list[dict]]:
    flow = []
    for i in range(240):  # 4 hours at 1 minute
        item = {
            "Timestamp": _ts(T0 + timedelta(minutes=i)),
            "Value": 10.0 + (i % 7),
            "Good": True,
            "Questionable": False,
            "Substituted": False,
        }
        if i == 30:
            item["Value"] = {"Name": "I/O Timeout", "Value": 246, "IsSystem": True}
            item["Good"] = False
        if i == 31:
            item["Questionable"] = True
        flow.append(item)
    temp = []
    for i in range(60):  # every 4 minutes, with three values at one time across a page break
        t = T0 + timedelta(minutes=4 * i)
        temp.append({"Timestamp": _ts(t), "Value": 300.0 + i, "Good": True})
        if i == 12:
            for k in range(2):
                temp.append({"Timestamp": _ts(t), "Value": 400.0 + k, "Good": True})
    valve = [
        {"Timestamp": _ts(T0 + timedelta(minutes=10 * i)), "Value": {"Name": s, "Value": v}}
        for i, (s, v) in enumerate([("Closed", 0), ("Open", 1)] * 12)
    ]
    return {"W-FIC101": flow, "W-TI102": temp, "W-XV103": valve, "W-ATTR": flow[:20]}


POINTS = {
    "\\\\PISRV\\FIC101.PV": {
        "WebId": "W-FIC101",
        "Name": "FIC101.PV",
        "Descriptor": "Feed flow",
        "EngineeringUnits": "m3/h",
        "Zero": 0.0,
        "Span": 50.0,
    },
    "\\\\PISRV\\TI102.PV": {
        "WebId": "W-TI102",
        "Name": "TI102.PV",
        "Descriptor": "Reactor temperature",
        "EngineeringUnits": "degC",
        "Zero": 0.0,
        "Span": 0.0,
    },
    "\\\\PISRV\\XV103.ST": {"WebId": "W-XV103", "Name": "XV103.ST", "Descriptor": "Feed valve"},
}
EVENT_FRAMES = [
    {
        "Name": "L-001",
        "StartTime": _ts(T0),
        "EndTime": _ts(T0 + timedelta(hours=2)),
        "TemplateName": "Batch",
    },
    {
        "Name": "L-002",
        "StartTime": _ts(T0 + timedelta(hours=2)),
        "EndTime": _ts(T0 + timedelta(hours=4)),
        "TemplateName": "Batch",
    },
    {
        "Name": "L-003",
        "StartTime": _ts(T0 + timedelta(hours=4)),
        "EndTime": "9999-12-31T23:59:59Z",
        "TemplateName": "Batch",
    },
    {
        "Name": "CIP-1",
        "StartTime": _ts(T0 + timedelta(hours=1)),
        "EndTime": _ts(T0 + timedelta(hours=1, minutes=30)),
        "TemplateName": "Cleaning",
    },
]


class FakePIWebAPI:
    """Serves on 127.0.0.1; ``auth`` is "none", "basic" or "bearer"."""

    def __init__(self, auth: str = "basic", token: str = "tok") -> None:
        self.auth, self.token = auth, token
        self.series = _series()
        self.requests: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                server.requests.append(self.path)
                if not server._authorized(self.headers.get("authorization", "")):
                    return self._send(401, {"Errors": ["Unauthorized"]})
                url = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                status, body = server.route(url.path.removeprefix("/piwebapi/"), q)
                self._send(status, body)

            def _send(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/piwebapi"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> FakePIWebAPI:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _authorized(self, header: str) -> bool:
        if self.auth == "none":
            return True
        if self.auth == "bearer":
            return header == f"Bearer {self.token}"
        expected = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return header == f"Basic {expected}"

    def route(self, path: str, q: dict[str, str]) -> tuple[int, dict]:
        not_found = (404, {"Errors": [f"not found: {q.get('path', path)}"]})
        if path in ("", "/"):
            return 200, {"Links": {"Self": self.url + "/"}}
        if path == "points":
            point = POINTS.get(q["path"])
            return (200, {**point, "Path": q["path"]}) if point else not_found
        if path == "attributes":
            if q["path"] != "\\\\AFSRV\\Plant\\Reactor|Feed Flow":
                return not_found
            return 200, {"WebId": "W-ATTR", "Name": "Feed Flow", "DefaultUnitsName": "m3/h"}
        if path == "dataservers":
            return (
                (200, {"WebId": "DS1", "Name": "PISRV"}) if q["path"] == "\\\\PISRV" else not_found
            )
        if path == "dataservers/DS1/points":
            pattern = q["nameFilter"].rstrip("*")
            hits = [{**p, "Path": k} for k, p in POINTS.items() if p["Name"].startswith(pattern)]
            i, n = int(q.get("startIndex", 0)), int(q.get("maxCount", 1000))
            return 200, {"Items": hits[i : i + n]}
        if path == "assetdatabases":
            return (200, {"WebId": "DB1"}) if q["path"] == "\\\\AFSRV\\Plant" else not_found
        if path == "assetdatabases/DB1/eventframes":
            start, end = _parse(q["startTime"]), _parse(q["endTime"])
            frames = [
                f
                for f in EVENT_FRAMES
                if _parse(f["StartTime"]) < end
                and _parse(f["EndTime"]) > start
                and f["TemplateName"] == q.get("templateName", f["TemplateName"])
            ]
            i, n = int(q.get("startIndex", 0)), int(q.get("maxCount", 1000))
            return 200, {"Items": frames[i : i + n]}
        if path.startswith("streams/"):
            _, web_id, kind = path.split("/")
            items = self.series.get(web_id)
            if items is None:
                return not_found
            start, end = _parse(q["startTime"]), _parse(q["endTime"])
            inside = [i for i in items if start <= _parse(i["Timestamp"]) <= end]
            if kind == "recorded":
                return 200, {"Items": inside[: int(q.get("maxCount", 1000))]}
            if kind == "interpolated":
                step = timedelta(minutes=int(q["interval"].removesuffix("m")))
                out, t = [], start
                while t <= end:
                    before = [i for i in items if _parse(i["Timestamp"]) <= t]
                    if before:
                        out.append({**before[-1], "Timestamp": _ts(t)})
                    t += step
                return 200, {"Items": out}
        return not_found
