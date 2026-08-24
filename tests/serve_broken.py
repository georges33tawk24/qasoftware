"""The broken fixture's HTTP behaviour: soft 404s, an exposed dotfile, a redirect chain,
and a cookie with no flags. Server-side defects need a server to plant them."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

SOFT_404_BODY = (
    b"<!doctype html><html lang=en><head><title>Not found</title></head>"
    b"<body><h1>Sorry, we could not find that page</h1></body></html>"
)
DOTENV_BODY = b"DATABASE_URL=postgres://user:hunter2@db.internal/prod\nSTRIPE_KEY=sk_live_x\n"


class BrokenHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/.env":
            self._send(200, DOTENV_BODY)
            return
        if path.startswith("/broken/redirect/"):
            step = int(path.rsplit("/", 1)[-1])
            target = f"/broken/redirect/{step - 1}" if step > 1 else "/broken/deep.html"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        candidate = Path(self.directory) / path.lstrip("/")
        if not candidate.is_file():
            # A catch-all SPA route: extensionless paths get the app shell with a 200,
            # real files 404 properly. That plants the soft-404 without hiding the
            # broken link, which is what makes both findings testable.
            if "." in path.rsplit("/", 1)[-1]:
                self._send(404, b"<!doctype html><title>404</title><h1>Not found</h1>")
            else:
                self._send(200, SOFT_404_BODY)
            return
        if self.command == "GET":
            super().do_GET()
        else:
            super().do_HEAD()

    def end_headers(self) -> None:
        self.send_header("Set-Cookie", "fixture_session=abc123; Path=/")
        super().end_headers()


def serve(root: Path) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(BrokenHandler, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"
