"""Browser drivers — SPEC §5.

One interface, four implementations behind a config value. Nothing above this layer
knows which driver is in use; that is what makes the stealth work in phase 10 a config
change rather than a rewrite.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from engine.artifact.models import Viewport
from engine.capture.auth import ANONYMOUS, Persona, apply_cookies, context_options
from engine.capture.secrets import Redactor

_INIT_SCRIPTS = ("vitals.js",)


@dataclass(frozen=True)
class ContextOptions:
    """Everything that must be pinned for a capture to be reproducible (SPEC §5)."""

    viewport: Viewport
    persona: Persona = field(default_factory=lambda: ANONYMOUS)
    locale: str = "en-GB"
    timezone_id: str = "UTC"
    ignore_https_errors: bool = False
    colour_scheme: Literal["light", "dark"] = "light"
    """Pinned, never inherited from the machine — a capture that changes with the CI
    box's appearance setting is not reproducible. Sites with a dark theme set it."""

    extra: dict[str, Any] = field(default_factory=dict)


class BrowserDriver(ABC):
    name: ClassVar[str]

    def __init__(self, *, headless: bool = True, profile_dir: Path | None = None) -> None:
        self.headless = headless
        self.profile_dir = profile_dir
        self._playwright: Playwright | None = None

    @abstractmethod
    async def launch(self) -> None: ...

    @abstractmethod
    async def new_context(self, options: ContextOptions, redactor: Redactor) -> BrowserContext: ...

    async def new_page(self, context: BrowserContext) -> Page:
        return await context.new_page()

    @abstractmethod
    async def close(self) -> None: ...


def _base_kwargs(options: ContextOptions) -> dict[str, Any]:
    return {
        "viewport": {"width": options.viewport.width, "height": options.viewport.height},
        "device_scale_factor": options.viewport.deviceScaleFactor,
        "locale": options.locale,
        "timezone_id": options.timezone_id,
        "reduced_motion": "reduce",
        "color_scheme": options.colour_scheme,
        "ignore_https_errors": options.ignore_https_errors,
        **options.extra,
    }


async def _prepare(context: BrowserContext, options: ContextOptions, redactor: Redactor) -> None:
    here = Path(__file__).parent
    for script in _INIT_SCRIPTS:
        await context.add_init_script((here / script).read_text())
    await apply_cookies(context, options.persona, redactor)


class PlaywrightDriver(BrowserDriver):
    """Default: headless Chromium."""

    name = "playwright"

    def __init__(self, *, headless: bool = True, profile_dir: Path | None = None) -> None:
        super().__init__(headless=headless, profile_dir=profile_dir)
        self._browser: Browser | None = None

    async def launch(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)

    async def new_context(self, options: ContextOptions, redactor: Redactor) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("launch() first")
        context = await self._browser.new_context(
            **_base_kwargs(options), **context_options(options.persona, redactor)
        )
        await _prepare(context, options, redactor)
        return context

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


class PlaywrightHeadedDriver(BrowserDriver):
    """Headed Chromium on a persistent profile.

    SPEC §5 order of operations: this clears a surprising amount of milder bot detection
    and should be tried before any stealth driver. A persistent profile *is* the context,
    so every call returns the same one.
    """

    name = "playwright_headed"

    def __init__(self, *, headless: bool = False, profile_dir: Path | None = None) -> None:
        super().__init__(headless=False, profile_dir=profile_dir or Path(".bureau/profile"))
        self._context: BrowserContext | None = None

    async def launch(self) -> None:
        self._playwright = await async_playwright().start()

    async def new_context(self, options: ContextOptions, redactor: Redactor) -> BrowserContext:
        if self._playwright is None:
            raise RuntimeError("launch() first")
        if self._context is None:
            assert self.profile_dir is not None
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=False,
                **_base_kwargs(options),
                **context_options(options.persona, redactor),
            )
            await _prepare(self._context, options, redactor)
        return self._context

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


class DriverUnavailable(RuntimeError):
    """The driver is implemented; the thing it drives is not installed or not reachable.

    Distinct from a bug on purpose: the answer is `pip install` or an environment
    variable, and a stack trace about a missing module does not say that.
    """


class PatchrightDriver(BrowserDriver):
    """Drop-in stealth Playwright; strips the automation fingerprints Chromium leaks.

    Same API as Playwright — it is a fork of the same package — so this is the default
    driver with one import changed. Read `docs/bot-protection.md` before reaching for it:
    an allowlist keeps working and evasion breaks every few weeks.
    """

    name = "patchright"

    def __init__(self, *, headless: bool = True, profile_dir: Path | None = None) -> None:
        super().__init__(headless=headless, profile_dir=profile_dir)
        self._browser: Browser | None = None

    async def launch(self) -> None:
        try:
            from patchright.async_api import async_playwright as patchright_playwright
        except ImportError as exc:
            raise DriverUnavailable(
                "the patchright driver needs `pip install bureau-engine[stealth]` "
                "and `patchright install chromium`"
            ) from exc
        self._playwright = await patchright_playwright().start()
        # Patchright's own guidance: a persistent context with no explicit channel is the
        # configuration it hardens. Headless is the single loudest signal, so a profile
        # directory turns this into headed-with-a-profile, which SPEC §5 asks for first.
        self._browser = await self._playwright.chromium.launch(headless=self.headless)

    async def new_context(self, options: ContextOptions, redactor: Redactor) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("launch() first")
        context = await self._browser.new_context(
            **_base_kwargs(options), **context_options(options.persona, redactor)
        )
        await _prepare(context, options, redactor)
        return context

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


class CamoufoxDriver(BrowserDriver):
    """A hardened Firefox build, for detection patchright does not clear.

    Camoufox launches its own browser and hands back a Playwright `Browser`, so
    everything downstream — contexts, snapshot script, checkers — is unchanged.
    """

    name = "camoufox"

    def __init__(self, *, headless: bool = True, profile_dir: Path | None = None) -> None:
        super().__init__(headless=headless, profile_dir=profile_dir)
        self._camoufox: Any = None
        self._browser: Browser | None = None

    async def launch(self) -> None:
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise DriverUnavailable(
                "the camoufox driver needs `pip install bureau-engine[stealth]` "
                "and `camoufox fetch`"
            ) from exc
        self._camoufox = AsyncCamoufox(headless=self.headless, humanize=True)
        self._browser = await self._camoufox.__aenter__()

    async def new_context(self, options: ContextOptions, redactor: Redactor) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("launch() first")
        kwargs = _base_kwargs(options)
        # Camoufox chooses its own locale, timezone and screen to match the fingerprint it
        # is presenting. Overriding them here is how a "stealth" run announces itself.
        for pinned in ("locale", "timezone_id"):
            kwargs.pop(pinned, None)
        context = await self._browser.new_context(
            **kwargs, **context_options(options.persona, redactor)
        )
        await _prepare(context, options, redactor)
        return context

    async def close(self) -> None:
        if self._camoufox is not None:
            await self._camoufox.__aexit__(None, None, None)
            self._camoufox = None
            self._browser = None


class RemoteDriver(BrowserDriver):
    """Browserbase, Steel, or a self-hosted grid.

    `BUREAU_REMOTE_WS` is the endpoint; `BUREAU_REMOTE_KIND` chooses how to attach —
    `cdp` for a Chrome DevTools endpoint (Browserbase, Steel, browserless), `ws` for a
    Playwright server. The URL usually carries the API key, so it is read from the
    environment and never from project config.
    """

    name = "remote"

    ENDPOINT_ENV = "BUREAU_REMOTE_WS"
    KIND_ENV = "BUREAU_REMOTE_KIND"

    def __init__(self, *, headless: bool = True, profile_dir: Path | None = None) -> None:
        super().__init__(headless=headless, profile_dir=profile_dir)
        self._browser: Browser | None = None

    async def launch(self) -> None:
        endpoint = os.environ.get(self.ENDPOINT_ENV, "")
        if not endpoint:
            raise DriverUnavailable(
                f"the remote driver needs {self.ENDPOINT_ENV} set to a browser endpoint"
            )
        self._playwright = await async_playwright().start()
        kind = os.environ.get(self.KIND_ENV, "cdp").lower()
        try:
            if kind == "cdp":
                self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
            else:
                self._browser = await self._playwright.chromium.connect(endpoint)
        except PlaywrightError as exc:
            raise DriverUnavailable(f"could not reach the remote browser: {exc}") from exc

    async def new_context(self, options: ContextOptions, redactor: Redactor) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("launch() first")
        # A CDP attach often lands on a browser that already has a context; reusing it is
        # what the hosted providers expect, and making a second one silently loses the
        # session they set up.
        if self._browser.contexts:
            context = self._browser.contexts[0]
        else:
            context = await self._browser.new_context(
                **_base_kwargs(options), **context_options(options.persona, redactor)
            )
        await _prepare(context, options, redactor)
        return context

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


DRIVERS: dict[str, type[BrowserDriver]] = {
    d.name: d
    for d in (
        PlaywrightDriver,
        PlaywrightHeadedDriver,
        PatchrightDriver,
        CamoufoxDriver,
        RemoteDriver,
    )
}


def get_driver(name: str, **kwargs: Any) -> BrowserDriver:
    try:
        return DRIVERS[name](**kwargs)
    except KeyError:
        raise ValueError(f"unknown driver {name!r}; have {sorted(DRIVERS)}") from None
