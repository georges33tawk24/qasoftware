"""A stand-in for the five HTTP trackers — SPEC §14.

Enough of Jira, OpenProject, Azure DevOps, Linear and GitHub to exercise a real round
trip: create, update by remote key, and (where the API has one) an attachment upload.
It records what it received so a test can assert on the shape that went over the wire,
which is the part that silently rots.

Not a mock: the adapters make real HTTP requests to it and read real responses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


@dataclass
class Record:
    method: str
    path: str
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    filename: str = ""


@dataclass
class State:
    calls: list[Record] = field(default_factory=list)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    attachments: dict[str, list[str]] = field(default_factory=dict)
    next_id: int = 100

    def create(self, payload: dict[str, Any], prefix: str = "QA") -> str:
        self.next_id += 1
        key = f"{prefix}-{self.next_id}"
        self.items[key] = payload
        return key

    def of(self, kind: str) -> list[Record]:
        return [c for c in self.calls if kind in c.path]


STATE = State()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    # ------------------------------------------------------------------ plumbing

    def _read(self) -> tuple[Any, str]:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        kind = self.headers.get("content-type") or ""
        if kind.startswith("multipart/form-data"):
            match = re.search(rb'filename="([^"]+)"', raw)
            return None, match.group(1).decode() if match else "?"
        try:
            return json.loads(raw) if raw else None, ""
        except json.JSONDecodeError:
            return raw.decode(errors="replace"), ""

    def _send(self, status: int, payload: Any) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, body: Any, filename: str) -> Record:
        entry = Record(
            method=self.command,
            path=self.path,
            body=body,
            headers={k.lower(): v for k, v in self.headers.items()},
            filename=filename,
        )
        STATE.calls.append(entry)
        return entry

    # ------------------------------------------------------------------- routes

    def do_POST(self) -> None:
        body, filename = self._read()
        self._record(body, filename)
        path = self.path.split("?")[0]

        if match := re.match(r"^/rest/api/3/issue/([^/]+)/attachments$", path):
            STATE.attachments.setdefault(match.group(1), []).append(filename)
            return self._send(200, [{"filename": filename}])
        if path == "/rest/api/3/issue":
            if not (body or {}).get("fields", {}).get("project", {}).get("key"):
                return self._send(400, {"errorMessages": ["project key is required"]})
            key = STATE.create(body)
            return self._send(201, {"id": key, "key": key, "self": f"/rest/api/3/issue/{key}"})

        if match := re.match(r"^/repos/([^/]+)/([^/]+)/issues$", path):
            key = STATE.create(body, prefix="gh")
            number = int(key.split("-")[1])
            return self._send(
                201, {"number": number, "html_url": f"https://github.test/i/{number}"}
            )

        if path == "/graphql":
            return self._linear(body)

        if path == "/api/v3/work_packages":
            key = STATE.create(body, prefix="wp")
            return self._send(201, {"id": int(key.split("-")[1]), "lockVersion": 1})
        if match := re.match(r"^/api/v3/work_packages/([^/]+)/attachments$", path):
            STATE.attachments.setdefault(match.group(1), []).append(filename)
            return self._send(201, {"id": 1, "fileName": filename})

        if "/_apis/wit/workitems" in path:
            key = STATE.create({"operations": body}, prefix="ado")
            return self._send(200, {"id": int(key.split("-")[1])})

        self._send(404, {"error": path})

    def do_PUT(self) -> None:
        body, filename = self._read()
        self._record(body, filename)
        path = self.path.split("?")[0]
        if match := re.match(r"^/rest/api/3/issue/([^/]+)$", path):
            STATE.items[match.group(1)] = body
            return self._send(204, None)
        self._send(404, {"error": path})

    def do_PATCH(self) -> None:
        body, filename = self._read()
        self._record(body, filename)
        path = self.path.split("?")[0]
        if match := re.match(r"^/repos/[^/]+/[^/]+/issues/(\d+)$", path):
            number = int(match.group(1))
            return self._send(
                200, {"number": number, "html_url": f"https://github.test/i/{number}"}
            )
        if match := re.match(r"^/api/v3/work_packages/(\d+)$", path):
            return self._send(200, {"id": int(match.group(1)), "lockVersion": 2})
        if match := re.match(r"^/[^/]+/_apis/wit/workitems/(\d+)$", path):
            return self._send(200, {"id": int(match.group(1))})
        self._send(404, {"error": path})

    def do_GET(self) -> None:
        self._record(None, "")
        path = self.path.split("?")[0]
        if match := re.match(r"^/api/v3/work_packages/(\d+)$", path):
            return self._send(200, {"id": int(match.group(1)), "lockVersion": 7})
        self._send(404, {"error": path})

    # ------------------------------------------------------------------- linear

    def _linear(self, body: Any) -> None:
        query = (body or {}).get("query") or ""
        field_name = "issueUpdate" if "issueUpdate" in query else "issueCreate"
        if field_name == "issueCreate" and not (body["variables"]["input"].get("teamId")):
            return self._send(200, {"errors": [{"message": "teamId is required"}]})
        key = STATE.create(body, prefix="LIN")
        self._send(
            200,
            {
                "data": {
                    field_name: {
                        "success": True,
                        "issue": {
                            "id": key,
                            "identifier": key,
                            "url": f"https://linear.test/{key}",
                        },
                    }
                }
            },
        )


def serve() -> tuple[ThreadingHTTPServer, str, State]:
    STATE.calls.clear()
    STATE.items.clear()
    STATE.attachments.clear()
    STATE.next_id = 100
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}", STATE
