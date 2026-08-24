"""Auth personas — SPEC §5. Not a later feature: the IDOR probes in §8.4 I need two
personas, so multi-persona support exists from the first capture.
"""

from __future__ import annotations

import base64
import hmac
import struct
import time
from hashlib import sha1
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page
from pydantic import BaseModel, ConfigDict, Field

from engine.capture.secrets import Redactor, resolve


class AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BasicAuth(AuthModel):
    usernameRef: str
    passwordRef: str


class FormLogin(AuthModel):
    """A scripted login. Selectors are the project's; values are secret references."""

    url: str
    usernameSelector: str
    passwordSelector: str
    usernameRef: str
    passwordRef: str
    submitSelector: str | None = None
    totpSelector: str | None = None
    totpSecretRef: str | None = None
    successSelector: str | None = None


class SessionCheck(AuthModel):
    """How to tell a live session from an expired one before a run starts."""

    url: str | None = None
    loggedInSelector: str | None = None
    loggedOutSelector: str | None = None


class Cookie(AuthModel):
    name: str
    valueRef: str
    domain: str
    path: str = "/"
    secure: bool = True
    httpOnly: bool = False


class Persona(AuthModel):
    name: str
    storageStatePath: str | None = None
    login: FormLogin | None = None
    basicAuth: BasicAuth | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    """Header values are secret references, resolved at run time."""

    cookies: list[Cookie] = Field(default_factory=list)
    sessionCheck: SessionCheck | None = None


ANONYMOUS = Persona(name="anonymous")


def totp(secret_b32: str, *, at: int | None = None, digits: int = 6, period: int = 30) -> str:
    """RFC 6238, SHA-1. Fifteen lines of stdlib beats a dependency."""
    key = base64.b32decode(secret_b32.replace(" ", "").upper() + "=" * (-len(secret_b32) % 8))
    counter = struct.pack(">Q", int(at if at is not None else time.time()) // period)
    digest = hmac.new(key, counter, sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**digits).zfill(digits)


def context_options(persona: Persona, redactor: Redactor) -> dict[str, Any]:
    """Playwright context kwargs for a persona. Registers every secret for redaction."""
    options: dict[str, Any] = {}

    if persona.basicAuth:
        username = resolve(persona.basicAuth.usernameRef)
        password = resolve(persona.basicAuth.passwordRef)
        redactor.add(password, base64.b64encode(f"{username}:{password}".encode()).decode())
        options["http_credentials"] = {"username": username, "password": password}

    if persona.headers:
        resolved = {name: resolve(ref) for name, ref in persona.headers.items()}
        redactor.add(*resolved.values())
        options["extra_http_headers"] = resolved

    state = storage_state_path(persona)
    if state and state.is_file():
        options["storage_state"] = str(state)

    return options


def storage_state_path(persona: Persona) -> Path | None:
    return Path(persona.storageStatePath) if persona.storageStatePath else None


async def apply_cookies(context: BrowserContext, persona: Persona, redactor: Redactor) -> None:
    if not persona.cookies:
        return
    payload = []
    for cookie in persona.cookies:
        value = resolve(cookie.valueRef)
        redactor.add(value)
        payload.append(
            {
                "name": cookie.name,
                "value": value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "httpOnly": cookie.httpOnly,
            }
        )
    await context.add_cookies(payload)  # type: ignore[arg-type]  # playwright TypedDict


async def session_valid(page: Page, persona: Persona) -> bool:
    """True when the persona still has a live session. No check configured means we
    have nothing to verify, so we assume valid rather than log in pointlessly."""
    check = persona.sessionCheck
    if check is None or (check.loggedInSelector is None and check.loggedOutSelector is None):
        return True
    if check.url:
        await page.goto(check.url, wait_until="domcontentloaded")
    if check.loggedOutSelector and await page.locator(check.loggedOutSelector).count():
        return False
    if check.loggedInSelector:
        return bool(await page.locator(check.loggedInSelector).count())
    return True


async def log_in(page: Page, persona: Persona, redactor: Redactor) -> None:
    login = persona.login
    if login is None:
        raise ValueError(f"persona {persona.name!r} has no login flow to run")

    username = resolve(login.usernameRef)
    password = resolve(login.passwordRef)
    redactor.add(password)

    await page.goto(login.url, wait_until="domcontentloaded")
    await page.fill(login.usernameSelector, username)
    await page.fill(login.passwordSelector, password)
    if login.submitSelector:
        await page.click(login.submitSelector)
    else:
        await page.press(login.passwordSelector, "Enter")

    if login.totpSelector and login.totpSecretRef:
        seed = resolve(login.totpSecretRef)
        redactor.add(seed)
        await page.wait_for_selector(login.totpSelector, timeout=15_000)
        await page.fill(login.totpSelector, totp(seed))
        await page.press(login.totpSelector, "Enter")

    if login.successSelector:
        await page.wait_for_selector(login.successSelector, timeout=30_000)
    else:
        await page.wait_for_load_state("networkidle")


async def ensure_authenticated(page: Page, persona: Persona, redactor: Redactor) -> None:
    """Check the session before the run, and re-auth on a detected logout (SPEC §5)."""
    if persona.name == ANONYMOUS.name and persona.login is None:
        return
    if await session_valid(page, persona):
        return
    await log_in(page, persona, redactor)
    state = storage_state_path(persona)
    if state:
        state.parent.mkdir(parents=True, exist_ok=True)
        await page.context.storage_state(path=str(state))
