"""The journeys themselves — SPEC §8.4 H.

Every action in here goes through `Flow.step`, so the reproduction steps on any resulting
Issue are the log, not a description someone wrote afterwards (SPEC §12.3).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Dialog
from playwright.async_api import Error as PlaywrightError

from engine.artifact.context import RunContext
from engine.capture.auth import Persona
from engine.capture.flows.discovery import Form, discover, login_form, pages_with
from engine.capture.flows.runner import FlowSpec
from engine.capture.flows.steps import Flow
from engine.capture.secrets import resolve

LONG_STRING = "A" * 5000
UNICODE = "Ünïcödé مرحبا بالعالم 👋"
SCRIPT_PAYLOAD = "<script>alert(1)</script>"
SQL_PAYLOAD = "' OR 1=1 --"
BAD_EMAIL = "not-an-email"

FORMAT_PROBES = {
    "email": BAD_EMAIL,
    "url": "not a url",
    "tel": "abc",
    "number": "not-a-number",
}

CART_HINTS = ("cart", "basket", "bag", "checkout")
SEARCH_HINTS = ("search", "results")


# ------------------------------------------------------------------ login flows


def login_flows(ctx: RunContext, persona: Persona, forms: list[Form]) -> list[FlowSpec]:
    """SPEC §8.4 H: valid, wrong password, unknown user, empty, session persists on
    refresh, logout actually clears the session."""
    form = login_form(forms)
    if form is None:
        return []
    email = form.by_type("email") or next(
        (f for f in form.textual if f.info.type != "password"), None
    )
    password = form.by_type("password")
    if email is None or password is None:
        return []

    credentials = _credentials(persona)
    specs = [
        FlowSpec(
            name="Sign in with an empty form",
            kind="auth",
            page_id=form.pageId,
            body=_empty_login(form, email.selector),
        ),
        FlowSpec(
            name="Sign in with an unknown email address",
            kind="auth",
            page_id=form.pageId,
            body=_bad_login(
                form,
                email.selector,
                password.selector,
                "nobody-here@example.invalid",
                "any-password-at-all",
                "unknown-user",
            ),
        ),
    ]
    if credentials:
        user, secret = credentials
        specs.append(
            FlowSpec(
                name="Sign in with the wrong password",
                kind="auth",
                page_id=form.pageId,
                body=_bad_login(
                    form,
                    email.selector,
                    password.selector,
                    user,
                    secret + "-wrong",
                    "wrong-password",
                ),
            )
        )
        specs.append(
            FlowSpec(
                name="Sign in, stay signed in, sign out",
                kind="auth",
                page_id=form.pageId,
                body=_session_lifecycle(form, email.selector, password.selector, user, secret),
            )
        )
    return specs


def _credentials(persona: Persona) -> tuple[str, str] | None:
    login = persona.login
    if login is None:
        return None
    try:
        return resolve(login.usernameRef), resolve(login.passwordRef)
    except Exception:
        return None


def _empty_login(form: Form, email_selector: str) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        await flow.submit(form.submit or "button[type=submit]", described="Submit with no details")
        await flow.settle()
        if flow.page.url.rstrip("/") != form.url.rstrip("/"):
            flow.fail(
                "empty-login-accepted",
                "Submitting the sign-in form with no details navigated away",
                expected="the form is rejected and stays put",
                actual=f"navigated to {flow.page.url}",
            )
            return
        await flow.note("Expect the form to say what is missing")
        if not await _has_validation(flow, email_selector):
            flow.fail(
                "no-required-validation",
                "The sign-in form accepted an empty submission with no message",
                expected="an error naming the missing field",
                actual="no visible error and no native validation",
            )

    return body


def _bad_login(
    form: Form, email_selector: str, password_selector: str, user: str, secret: str, kind: str
) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        await flow.fill(email_selector, user, described=f"Enter the email address {user}")
        await flow.fill(password_selector, secret, described="Enter a password")
        await flow.submit(form.submit or "button[type=submit]", described="Submit the sign-in form")
        await flow.settle()

        body_text = await flow.body_text()
        flow.data["message"] = await _first_error_text(flow) or _first_error(body_text)
        if _looks_signed_in(flow.page.url, form.url, body_text):
            flow.fail(
                f"{kind}-accepted",
                "The wrong credentials were accepted",
                expected="the sign-in is refused",
                actual=f"signed in and landed on {flow.page.url}",
                severityHint="blocker",
            )
            return
        await flow.note("Expect an error message explaining the refusal")
        if not flow.data["message"]:
            flow.fail(
                "no-error-message",
                "The sign-in was refused with no message on the page",
                expected="a visible error",
                actual="the form reappeared with nothing said",
            )

    return body


def _session_lifecycle(
    form: Form, email_selector: str, password_selector: str, user: str, secret: str
) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        await flow.fill(email_selector, user, described=f"Enter the email address {user}")
        await flow.fill(password_selector, secret, described="Enter the password")
        await flow.submit(form.submit or "button[type=submit]", described="Submit the sign-in form")
        await flow.settle()

        signed_in_url = flow.page.url
        if not _looks_signed_in(signed_in_url, form.url, await flow.body_text()):
            flow.fail(
                "valid-login-rejected",
                "Correct credentials did not sign in",
                expected="a signed-in page",
                actual=f"still on {signed_in_url}",
                abort=True,
            )

        await flow.reload()
        await flow.note("Expect the session to survive a refresh")
        if not _looks_signed_in(flow.page.url, form.url, await flow.body_text()):
            flow.fail(
                "session-lost-on-refresh",
                "The session did not survive a page refresh",
                expected="still signed in",
                actual=f"sent to {flow.page.url}",
            )

        logout = await _find_logout(flow)
        if logout is None:
            flow.fail(
                "no-way-to-sign-out",
                "No sign-out control could be found while signed in",
                expected="a way to sign out",
                actual="none found",
            )
            return
        session = await _session_header(flow)
        await flow.click(logout, described="Sign out")
        await flow.settle()
        await flow.goto(signed_in_url)
        await flow.note("Expect the signed-in page to be closed to us now")
        if _looks_signed_in(flow.page.url, form.url, await flow.body_text()):
            flow.fail(
                "logout-does-not-clear-browser-session",
                "The signed-in page is still reachable in the browser after signing out",
                expected="a redirect to sign in",
                actual=f"served {flow.page.url}",
            )

        # Clearing the cookie is not invalidating the session. Replaying the old token
        # is the only way to tell the two apart, and it is the one that matters.
        if session:
            await flow.note("Replay the old session token to see whether it still works")
            still_valid = await _replay(flow, signed_in_url, session)
            if still_valid:
                flow.fail(
                    "logout-does-not-invalidate-session",
                    "The session token still works after signing out",
                    expected="the token is rejected once signed out",
                    actual="the server answered the old token in full",
                )

    return body


# ------------------------------------------------------------------ form battery


def form_flows(forms: list[Form]) -> list[FlowSpec]:
    specs: list[FlowSpec] = []
    for form in forms:
        if form.kind in ("login", "search") or not form.textual:
            continue
        specs.append(
            FlowSpec(
                name=f"Submit {_name(form)} with nothing filled in",
                kind="form",
                page_id=form.pageId,
                body=_empty_form(form),
            )
        )
        if any(f.info.type in FORMAT_PROBES for f in form.textual):
            specs.append(
                FlowSpec(
                    name=f"Submit {_name(form)} with badly formatted values",
                    kind="form",
                    page_id=form.pageId,
                    body=_bad_formats(form),
                )
            )
        specs.append(
            FlowSpec(
                name=f"Submit {_name(form)} with awkward but valid values",
                kind="form",
                page_id=form.pageId,
                body=_awkward_values(form),
            )
        )
        specs.append(
            FlowSpec(
                name=f"Submit {_name(form)} twice in quick succession",
                kind="form",
                page_id=form.pageId,
                body=_double_submit(form),
            )
        )
    return specs


def _name(form: Form) -> str:
    label = form.element.htmlId or form.element.form.name if form.element.form else None
    return f"the {label} form" if label else "the form"


def _empty_form(form: Form) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        if not form.required:
            await flow.note("No field is marked required, so there is nothing to enforce")
            return
        await flow.submit(form.submit or "button[type=submit]", described="Submit with no details")
        await flow.settle()
        await flow.note("Expect the form to say which field is missing")
        first = form.required[0]
        if await _has_validation(flow, first.selector):
            named = await _error_names_a_field(flow, [f.label for f in form.required])
            if not named:
                flow.fail(
                    "error-names-no-field",
                    "The form refused the submission without saying which field was wrong",
                    expected="an error that names the field",
                    actual=(await _first_error_text(flow)) or "a message with no field named",
                )
            return
        flow.fail(
            "no-required-validation",
            f"{len(form.required)} required field(s) were not enforced",
            expected="the submission is refused",
            actual="the form submitted with empty required fields",
            fields=[f.label for f in form.required],
        )

    return body


def _plausible(candidate: Any) -> str:
    kind = candidate.info.type
    if kind == "email":
        return "flow@example.test"
    if kind == "number":
        return "1"
    if kind == "url":
        return "https://example.test/"
    if kind == "tel":
        return "+441234567890"
    return "Flow test"


def _bad_formats(form: Form) -> Any:
    """Kept apart from the rest of the battery on purpose: a browser refuses to submit a
    form containing an invalid `type=email`, so mixing this in would silently skip every
    other check in the same run."""

    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        probed = []
        for candidate in form.textual:
            payload = FORMAT_PROBES.get(candidate.info.type)
            if payload is None:
                continue
            probed.append(candidate)
            await flow.fill(
                candidate.selector,
                payload,
                described=f"Enter {payload!r} in {candidate.label}",
            )
        for candidate in form.textual:
            if candidate not in probed:
                await flow.fill(
                    candidate.selector,
                    _plausible(candidate),
                    described=f"Fill in {candidate.label}",
                )
        await flow.submit(form.submit or "button[type=submit]", described="Submit the form")
        await flow.settle()
        await flow.note("Expect the badly formatted values to be refused")
        for candidate in probed:
            if await _has_validation(flow, candidate.selector):
                continue
            flow.fail(
                "invalid-format-accepted",
                f"{candidate.label} accepted a value that is not a valid {candidate.info.type}",
                expected=f"a valid {candidate.info.type}",
                actual=f"{FORMAT_PROBES[candidate.info.type]!r} was accepted",
                field=candidate.label,
            )

    return body


def _awkward_values(form: Form) -> Any:
    async def body(flow: Flow) -> None:
        dialogs: list[str] = []

        async def on_dialog(dialog: Dialog) -> None:
            dialogs.append(dialog.message)
            await dialog.dismiss()

        flow.page.on("dialog", on_dialog)
        await flow.goto(form.url)

        for candidate in form.textual:
            if candidate.info.type in FORMAT_PROBES:
                await flow.fill(
                    candidate.selector,
                    _plausible(candidate),
                    described=f"Fill in {candidate.label}",
                )
        long_field = next((f for f in form.textual if f.info.type in ("text", "textarea")), None)
        if long_field is not None:
            await flow.fill(
                long_field.selector,
                LONG_STRING,
                described=f"Enter 5,000 characters in {long_field.label}",
            )
        for candidate, payload, described in _payloads(form):
            await flow.fill(candidate.selector, payload, described=described)

        await flow.submit(form.submit or "button[type=submit]", described="Submit the form")
        await flow.settle()

        rendered = await flow.page.content()
        if SCRIPT_PAYLOAD in rendered:
            flow.fail(
                "unescaped-input-reflected",
                "A submitted value was written back into the page without escaping",
                expected="the value appears as text",
                actual="the markup was rendered as markup",
                payload=SCRIPT_PAYLOAD,
            )
        if dialogs:
            flow.fail(
                "submitted-markup-executed",
                "A submitted value executed as script",
                expected="the value is treated as text",
                actual=f"a dialog appeared saying {dialogs[0]!r}",
            )
        body_text = await flow.body_text()
        if _looks_like_a_crash(body_text):
            flow.fail(
                "server-error-on-input",
                "An awkward but ordinary value produced a server error",
                expected="the input is rejected politely",
                actual=_first_error(body_text) or "a server error page",
            )

    return body


def _payloads(form: Form) -> list[tuple[Any, str, str]]:
    """Malformed-input handling, which is what SPEC §8.4 H asks for. The check is whether
    the site handles these safely, never whether anything can be extracted with them."""
    out = []
    plain = [f for f in form.textual if f.info.type in ("text", "textarea", "search")]
    if plain:
        out.append((plain[0], UNICODE, f"Enter unicode and right-to-left text in {plain[0].label}"))
    if plain:
        out.append((plain[0], SCRIPT_PAYLOAD, f"Enter a script tag in {plain[0].label}"))
    if len(plain) > 1:
        out.append((plain[1], SQL_PAYLOAD, f"Enter a quote and an OR clause in {plain[1].label}"))
    return out


def _double_submit(form: Form) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        for candidate in form.textual:
            value = (
                "double@example.test" if candidate.info.type == "email" else "Double submit test"
            )
            await flow.fill(candidate.selector, value, described=f"Fill in {candidate.label}")
        selector = form.submit or "button[type=submit]"
        await flow.step(
            "Click submit twice without waiting",
            lambda: flow.page.dblclick(selector, timeout=8000),
        )
        await flow.settle(600)
        await flow.note("Expect one submission, not two")
        flow.data["afterDoubleSubmit"] = flow.page.url

    return body


# --------------------------------------------------------------- cart and search


CART_JS = """
() => {
  const money = /[£$€]\\s?(\\d[\\d,]*(?:\\.\\d{1,2})?)/;
  const num = (t) => {
    const m = money.exec(t || '');
    return m ? parseFloat(m[1].replace(/,/g, '')) : null;
  };
  const rows = [];
  const rowSelector = 'tr, li, [class*=line], [class*=item]';
  for (const row of document.querySelectorAll(rowSelector)) {
    const cells = Array.from(row.children).map((c) => (c.textContent || '').trim());
    if (cells.length < 3) continue;
    const values = cells.map(num).filter((v) => v !== null);
    const quantities = cells
      .map((c) => (/^\\s*\\d+\\s*$/.test(c) ? parseInt(c, 10) : null))
      .filter((v) => v !== null);
    if (values.length >= 2 && quantities.length >= 1) {
      rows.push({
        unit: values[0],
        quantity: quantities[0],
        lineTotal: values[values.length - 1],
      });
    }
  }
  let displayed = null;
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const text = (el.textContent || '').trim();
    if (!/total/i.test(text)) continue;
    const value = num(text);
    if (value !== null) displayed = value;
  }
  return {rows, displayed};
}
"""


def cart_flows(ctx: RunContext) -> list[FlowSpec]:
    """SPEC §8.4 H: line-item arithmetic verified independently. This is the class of bug
    worth the most to catch, and the only way to catch it is to do the sum yourself."""
    return [
        FlowSpec(
            name=f"Check the totals on {page.path}",
            kind="cart",
            page_id=page.id,
            body=_cart_arithmetic(page.url),
        )
        for page in pages_with(ctx, CART_HINTS)
    ]


def _cart_arithmetic(url: str) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(url)
        await flow.settle(300)
        await flow.note("Read the line items and add them up independently")
        parsed = await flow.page.evaluate(CART_JS)
        rows = [r for r in parsed.get("rows") or [] if r.get("unit") and r.get("quantity")]
        displayed = parsed.get("displayed")
        flow.data["lines"] = rows
        flow.data["displayedTotal"] = displayed
        if not rows or displayed is None:
            await flow.note("No line items and total could be read here")
            return

        computed = round(sum(r["unit"] * r["quantity"] for r in rows), 2)
        flow.data["computedTotal"] = computed
        if abs(computed - float(displayed)) > 0.005:
            flow.fail(
                "total-does-not-match-line-items",
                "The displayed total is not the sum of the line items",
                expected=f"{computed:.2f}",
                actual=f"{float(displayed):.2f}",
                lines=rows,
            )
        for row in rows:
            if row.get("lineTotal") is None:
                continue
            expected = round(row["unit"] * row["quantity"], 2)
            if abs(expected - row["lineTotal"]) > 0.005:
                flow.fail(
                    "line-total-does-not-match",
                    "A line total is not its unit price times its quantity",
                    expected=f"{expected:.2f}",
                    actual=f"{row['lineTotal']:.2f}",
                    line=row,
                )

    return body


def search_flows(ctx: RunContext, forms: list[Form]) -> list[FlowSpec]:
    specs: list[FlowSpec] = []
    for form in forms:
        if form.kind != "search":
            continue
        specs.append(
            FlowSpec(
                name="Search for something that does not exist",
                kind="search",
                page_id=form.pageId,
                body=_no_results(form),
            )
        )
    for page in pages_with(ctx, SEARCH_HINTS):
        if "?" in page.url or any(f.pageId == page.id for f in forms):
            continue
        specs.append(
            FlowSpec(
                name=f"Search {page.path} for something that does not exist",
                kind="search",
                page_id=page.id,
                body=_no_results_by_url(page.url),
            )
        )
    return specs


NONSENSE = "zzqxwvunobodysearchesforthis"


def _no_results(form: Form) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(form.url)
        field = form.textual[0]
        await flow.fill(field.selector, NONSENSE, described="Search for a nonsense term")
        await flow.press(field.selector, "Enter")
        await flow.settle(400)
        await _expect_empty_state(flow)

    return body


def _no_results_by_url(url: str) -> Any:
    async def body(flow: Flow) -> None:
        await flow.goto(urljoin(url, f"?q={NONSENSE}"))
        await flow.settle(300)
        await _expect_empty_state(flow)

    return body


async def _expect_empty_state(flow: Flow) -> None:
    await flow.note("Expect the page to say there are no results")
    text = (await flow.body_text()).casefold()
    if any(phrase in text for phrase in ("no result", "nothing found", "no matches", "0 result")):
        return
    flow.fail(
        "no-empty-state",
        "A search with no results says nothing at all",
        expected="a message explaining there are no results",
        actual=(text.strip()[:120] or "an empty page"),
    )


# ---------------------------------------------------------------------- helpers


LOGOUT_SELECTORS = (
    "a[href*='logout' i]",
    "a[href*='signout' i]",
    "a[href*='sign-out' i]",
    "button[name*='logout' i]",
    "text=/^\\s*(sign out|log out|logout)\\s*$/i",
)


async def _session_header(flow: Flow) -> str:
    cookies = await flow.page.context.cookies()
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


async def _replay(flow: Flow, url: str, cookie: str) -> bool:
    """Ask the server directly, carrying the token the browser has just thrown away."""
    try:
        response = await flow.page.context.request.fetch(
            url,
            headers={"Cookie": cookie},
            timeout=8000,
            max_redirects=0,
            fail_on_status_code=False,
        )
    except PlaywrightError:
        return False
    if response.status >= 300:
        return False
    body = await response.text()
    return _looks_signed_in(url, "", body)


async def _find_logout(flow: Flow) -> str | None:
    for selector in LOGOUT_SELECTORS:
        try:
            if await flow.page.locator(selector).count():
                return selector
        except PlaywrightError:
            continue
    return None


async def _has_validation(flow: Flow, selector: str) -> bool:
    """Either the browser refused it or the page said something."""
    native = await flow.page.evaluate(
        "(sel) => { const el = document.querySelector(sel);"
        " return el && el.validity ? !el.validity.valid : false; }",
        selector,
    )
    return bool(native) or bool(await _first_error_text(flow))


async def _first_error_text(flow: Flow) -> str:
    for selector in ("[role=alert]", ".error", "[class*=error]", "[aria-invalid=true]"):
        text = await flow.text_of(selector)
        if text:
            return text[:200]
    return ""


async def _error_names_a_field(flow: Flow, labels: list[str]) -> bool:
    message = (await _first_error_text(flow)).casefold()
    if not message:
        return True  # native validation names the field for us
    return any(label.casefold() in message for label in labels if label)


def _first_error(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(
            marker in stripped.casefold()
            for marker in ("error", "invalid", "incorrect", "required", "wrong", "missing")
        ):
            return stripped[:200]
    return ""


def _looks_signed_in(url: str, login_url: str, body_text: str) -> bool:
    if url.rstrip("/") == login_url.rstrip("/"):
        return False
    lowered = body_text.casefold()
    return (
        any(marker in lowered for marker in ("sign out", "log out", "signed in", "your account"))
        or "account" in url.casefold()
    )


def _looks_like_a_crash(text: str) -> bool:
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in ("traceback (most recent call last)", "internal server error", "stack trace")
    )


def _refused(flow: Flow, body_text: str) -> bool:
    return bool(_first_error(body_text))


def build(ctx: RunContext, persona: Persona, *, shared: bool = True) -> list[FlowSpec]:
    """`shared` covers the journeys that do not depend on who is signed in.

    A contact form behaves the same for every persona, and running it once per persona
    produces the same finding several times over.
    """
    forms = discover(ctx)
    specs = list(login_flows(ctx, persona, forms))
    if shared:
        specs += [*form_flows(forms), *cart_flows(ctx), *search_flows(ctx, forms)]
    return specs
