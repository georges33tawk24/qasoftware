"""A small stateful web app with deliberate defects — the fixture the flow engine runs
against.

`tests/fixtures/site/` is static, which is enough for capture and for the deterministic
sweep but cannot fail a login. This can. Every defect it plants is listed in
`tests/fixtures/FLOWS.md` and asserted by `tests/test_flows.py`.

Deliberately small: a session store in a dict, no framework, no database.
"""

from __future__ import annotations

import html
import json
import secrets
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit

USERS = {"ada@example.test": "correct-horse", "grace@example.test": "battery-staple"}

ITEMS = {
    "ada@example.test": [{"id": 1, "title": "Ada's private note", "owner": "ada@example.test"}],
    "grace@example.test": [
        {"id": 2, "title": "Grace's private note", "owner": "grace@example.test"}
    ],
}

# DEFECT: the displayed total does not match the line items. 2×12.50 + 1×4.00 is 29.00.
CART_LINES: list[dict[str, Any]] = [
    {"sku": "TEA-01", "title": "Loose leaf tea", "unit": 12.50, "quantity": 2},
    {"sku": "CUP-02", "title": "Enamel mug", "unit": 4.00, "quantity": 1},
]
CART_DISPLAYED_TOTAL = 27.00

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="canonical" href="{path}">
<meta name="description" content="Fixture application page.">
<link rel="icon" href="data:,">
<style>body{{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:0;font-size:16px;
line-height:24px;color:#111}}main{{max-width:640px;margin:0 auto;padding:24px}}
label{{display:block;margin-bottom:8px;font-weight:600}}
input,textarea{{width:100%;padding:8px;margin-bottom:16px;border:1px solid #e5e7eb;
border-radius:6px;font:inherit}}
button{{font:inherit;padding:10px 18px;border:0;border-radius:6px;background:#1c64c8;
color:#fff;min-height:44px}}
.error{{color:#c7443a;font-weight:600}}nav a{{margin-right:16px}}</style></head>
<body><nav><a href="/app/">Home</a><a href="/app/login">Sign in</a>
<a href="/app/contact">Contact</a><a href="/app/cart">Cart</a></nav>
<main>{body}</main></body></html>
"""


@dataclass
class App:
    sessions: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    submissions: list[dict[str, str]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)
    rate_limit: int = 50
    """DEFECT: high enough that the login has no meaningful rate limit."""


def render(title: str, path: str, body: str) -> bytes:
    return PAGE.format(title=title, path=path, body=body).encode()


class Handler(BaseHTTPRequestHandler):
    app: App
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    # ---------------------------------------------------------------- plumbing

    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        self._send(
            status,
            json.dumps(payload).encode(),
            content_type="application/json",
            headers=headers,
        )

    def _redirect(self, to: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _session_user(self) -> str | None:
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "sid":
                return self.app.sessions.get(value)
        return None

    def _bearer(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        token = header.removeprefix("Bearer ").strip()
        return self.app.sessions.get(token) if token else None

    # ------------------------------------------------------------------ routes

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)
        user = self._session_user()

        if path in ("/app", "/app/"):
            return self._send(200, render("Fixture app", path, self._home(user)))
        if path == "/app/login":
            return self._send(200, render("Sign in", path, self._login_form()))
        if path == "/app/contact":
            return self._send(200, render("Contact us", path, self._contact_form()))
        if path == "/app/cart":
            return self._send(200, render("Your cart", path, self._cart()))
        if path == "/app/account":
            if not user:
                return self._redirect("/app/login")
            return self._send(
                200,
                render(
                    "Account",
                    path,
                    f"<h1>Account</h1><p>Signed in as {html.escape(user)}</p>"
                    '<p><a href="/app/logout" id="logout">Sign out</a></p>',
                ),
            )
        if path == "/app/logout":
            # DEFECT: the cookie is cleared in the browser but the session is never
            # invalidated server-side, so the old token still works.
            return self._redirect("/app/", {"Set-Cookie": "sid=; Path=/; Max-Age=0"})
        if path == "/app/search":
            return self._send(200, render("Search", path, self._search(query)))
        if path == "/api/items":
            # DEFECT: no authorisation check at all.
            owner = (query.get("owner") or [""])[0]
            return self._json(200, {"items": ITEMS.get(owner, [])})
        if path == "/api/me":
            token_user = self._bearer() or user
            if not token_user:
                return self._json(401, {"error": "unauthorised"})
            # DEFECT: any valid token can read any account by asking for it.
            asked = (query.get("email") or [token_user])[0]
            return self._json(
                200,
                {"email": asked, "phone": "+44 7700 900000", "items": ITEMS.get(asked, [])},
                {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true"},
            )
        if path == "/api/orders":
            try:
                page = int((query.get("page") or ["1"])[0])
            except ValueError:
                # DEFECT: the error response carries a stack trace.
                return self._json(
                    500,
                    {
                        "error": "ValueError: invalid literal for int() with base 10",
                        "trace": 'File "/srv/app/orders.py", line 42, in list_orders\\n'
                        "    page = int(request.args['page'])",
                    },
                )
            return self._json(200, {"page": page, "orders": []})
        return self._send(404, render("Not found", path, "<h1>Not found</h1>"))

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        form = self._form()

        if path == "/app/login":
            return self._do_login(form)
        if path == "/app/contact":
            return self._do_contact(form)
        if path.startswith("/api/"):
            return self._json(405, {"error": "method not allowed"})
        return self._send(404, render("Not found", path, "<h1>Not found</h1>"))

    def do_DELETE(self) -> None:
        # DEFECT: method tampering — DELETE is accepted on a read endpoint.
        if urlsplit(self.path).path == "/api/items":
            return self._json(200, {"deleted": True})
        return self._json(405, {"error": "method not allowed"})

    # ----------------------------------------------------------------- handlers

    def _do_login(self, form: dict[str, str]) -> None:
        email = form.get("email", "").strip()
        password = form.get("password", "")
        with self.app.lock:
            self.app.attempts[email] = self.app.attempts.get(email, 0) + 1
            attempts = self.app.attempts[email]

        if attempts > self.app.rate_limit:
            return self._send(
                429, render("Sign in", "/app/login", self._login_form("Too many attempts."))
            )
        if not email or not password:
            return self._send(
                200, render("Sign in", "/app/login", self._login_form("Enter your details."))
            )
        if USERS.get(email) != password:
            # DEFECT: the message tells an attacker which half was wrong.
            message = (
                "No account with that email address."
                if email not in USERS
                else "That password is incorrect."
            )
            return self._send(200, render("Sign in", "/app/login", self._login_form(message)))

        token = secrets.token_hex(16)
        with self.app.lock:
            self.app.sessions[token] = email
        return self._redirect("/app/account", {"Set-Cookie": f"sid={token}; Path=/"})

    def _do_contact(self, form: dict[str, str]) -> None:
        name = form.get("name", "").strip()
        email = form.get("email", "").strip()
        message = form.get("message", "").strip()
        if not name or not email:
            # DEFECT: the error names no field, so nothing marks which one is wrong.
            return self._send(
                200,
                render("Contact us", "/app/contact", self._contact_form("Something is missing.")),
            )
        with self.app.lock:
            self.app.submissions.append({"name": name, "email": email, "message": message})
        # DEFECT: the confirmation reflects the name without escaping it.
        return self._send(
            200,
            render(
                "Thank you",
                "/app/contact",
                f"<h1>Thank you</h1><p id='confirmation'>We will reply to {name} shortly.</p>",
            ),
        )

    # -------------------------------------------------------------------- views

    def _home(self, user: str | None) -> str:
        who = (
            f'<p>Signed in as {html.escape(user)} · <a href="/app/logout">Sign out</a></p>'
            if user
            else ""
        )
        return (
            "<h1>Fixture application</h1>"
            f"{who}<p>A small app with deliberate defects for the flow engine.</p>"
            '<p><a href="/app/login">Sign in</a> · <a href="/app/contact">Contact us</a> · '
            '<a href="/app/cart">Cart</a> · <a href="/app/search?q=tea">Search</a></p>'
            '<div id="feed"></div>'
            # The page calls its own API, which is how the probe engine comes to know
            # these endpoints exist at all (SPEC §8.4 I).
            "<script>"
            'fetch("/api/items?owner=ada@example.test").then(r=>r.json())'
            '.then(d=>{document.getElementById("feed").textContent='
            '"items: " + (d.items||[]).length;});'
            'fetch("/api/orders?page=1");'
            'fetch("/api/me");'
            "</script>"
        )

    def _login_form(self, error: str = "") -> str:
        banner = f'<p class="error" id="login-error">{html.escape(error)}</p>' if error else ""
        return (
            f"<h1>Sign in</h1>{banner}"
            '<form method="post" action="/app/login" id="login">'
            '<label for="email">Email address</label>'
            '<input id="email" name="email" type="email" required autocomplete="username">'
            '<label for="password">Password</label>'
            '<input id="password" name="password" type="password" required '
            'autocomplete="current-password" minlength="8">'
            '<button type="submit" id="login-submit">Sign in</button></form>'
        )

    def _contact_form(self, error: str = "") -> str:
        banner = f'<p class="error" id="contact-error">{html.escape(error)}</p>' if error else ""
        return (
            f"<h1>Contact us</h1>{banner}"
            '<form method="post" action="/app/contact" id="contact">'
            '<label for="name">Your name</label>'
            '<input id="name" name="name" type="text" required maxlength="80">'
            '<label for="email">Email address</label>'
            '<input id="email" name="email" type="email" required>'
            '<label for="message">Message</label>'
            '<textarea id="message" name="message" rows="4"></textarea>'
            '<button type="submit" id="contact-submit">Send</button></form>'
        )

    def _cart(self) -> str:
        rows = "".join(
            f'<tr class="line"><td class="title">{html.escape(line["title"])}</td>'
            f'<td class="unit">£{line["unit"]:.2f}</td>'
            f'<td class="quantity">{line["quantity"]}</td>'
            f'<td class="line-total">£{line["unit"] * line["quantity"]:.2f}</td></tr>'
            for line in CART_LINES
        )
        return (
            "<h1>Your cart</h1><table id='cart'>"
            "<tr><th>Item</th><th>Unit</th><th>Qty</th><th>Line total</th></tr>"
            f"{rows}</table>"
            f'<p id="total">Total £{CART_DISPLAYED_TOTAL:.2f}</p>'
            '<p><button type="button" id="checkout">Checkout</button></p>'
        )

    def _search(self, query: dict[str, list[str]]) -> str:
        term = (query.get("q") or [""])[0]
        if not term:
            return "<h1>Search</h1><p>Enter a search term.</p>"
        if term.strip().casefold() == "tea":
            return f"<h1>Search</h1><p id='results'>1 result for {html.escape(term)}</p>"
        # DEFECT: no empty state — a search with no results looks like a broken page.
        return "<h1>Search</h1>"


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, str, App]:
    app = App()
    handler = partial(Handler)
    Handler.app = app
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}", app
