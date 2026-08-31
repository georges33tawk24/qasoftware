"""Appium mobile automation driver — SPEC §19 (Phase 11).

Controls native Android and iOS applications over Appium's W3C WebDriver endpoint.
Captures full-screen frame buffers, XML accessibility hierarchies, and viewport sizes.
"""

from __future__ import annotations

import base64
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from engine.artifact.models import Viewport
from engine.capture.driver import DriverUnavailable


@dataclass
class MobileAppConfig:
    platform: Literal["android", "ios"] = "android"
    appium_url: str = "http://127.0.0.1:4723"
    app_path: str | None = None
    app_package: str | None = None
    app_activity: str | None = None
    bundle_id: str | None = None
    device_name: str = "MobileDevice"
    automation_name: str | None = None
    no_reset: bool = True
    auto_grant_permissions: bool = True
    new_command_timeout: int = 120
    extra_capabilities: dict[str, Any] | None = None


class AppiumDriver:
    """Mobile native driver over Appium."""

    def __init__(self, config: MobileAppConfig) -> None:
        self.config = config
        self._driver: Any = None

    def _build_capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "platformName": "Android" if self.config.platform == "android" else "iOS",
            "appium:deviceName": self.config.device_name,
            "appium:noReset": self.config.no_reset,
            "appium:newCommandTimeout": self.config.new_command_timeout,
        }

        if self.config.platform == "android":
            caps["appium:automationName"] = self.config.automation_name or "UiAutomator2"
            caps["appium:autoGrantPermissions"] = self.config.auto_grant_permissions
            if self.config.app_path:
                caps["appium:app"] = str(Path(self.config.app_path).resolve())
            if self.config.app_package:
                caps["appium:appPackage"] = self.config.app_package
            if self.config.app_activity:
                caps["appium:appActivity"] = self.config.app_activity
        else:
            caps["appium:automationName"] = self.config.automation_name or "XCUITest"
            if self.config.app_path:
                caps["appium:app"] = str(Path(self.config.app_path).resolve())
            if self.config.bundle_id:
                caps["appium:bundleId"] = self.config.bundle_id

        if self.config.extra_capabilities:
            caps.update(self.config.extra_capabilities)

        return caps

    async def launch(self) -> None:
        """Connect to the Appium server."""
        try:
            from appium import webdriver  # type: ignore[import-not-found]
            from appium.options.common import (  # type: ignore[import-not-found]
                AppiumOptions,
            )
        except ImportError as exc:
            raise DriverUnavailable(
                "the mobile driver needs `pip install appium-python-client` and an active "
                "Appium server (https://appium.io/docs/en/latest/quickstart/)"
            ) from exc

        caps = self._build_capabilities()
        options = AppiumOptions()
        options.load_capabilities(caps)

        try:
            # Appium python client uses synchronous WebDriver, wrapped cleanly here
            self._driver = webdriver.Remote(
                command_executor=self.config.appium_url,
                options=options,
            )
        except Exception as exc:
            raise DriverUnavailable(
                f"could not connect to Appium at {self.config.appium_url}: {exc}"
            ) from exc

    async def get_page_source(self) -> str:
        """Fetch the XML accessibility hierarchy."""
        if self._driver is None:
            raise RuntimeError("launch() first")
        return str(self._driver.page_source or "")

    async def get_screenshot(self) -> bytes:
        """Capture screen as PNG bytes."""
        if self._driver is None:
            raise RuntimeError("launch() first")
        raw_b64 = self._driver.get_screenshot_as_base64()
        return base64.b64decode(raw_b64)

    async def get_viewport(self) -> Viewport:
        """Determine device viewport and screen scale."""
        if self._driver is None:
            raise RuntimeError("launch() first")
        size = self._driver.get_window_size()
        width = int(size.get("width", 390))
        height = int(size.get("height", 844))
        name = "mobile_android" if self.config.platform == "android" else "mobile_ios"
        return Viewport(
            name=name,
            width=width,
            height=height,
            deviceScaleFactor=2.0,
        )

    async def close(self) -> None:
        """Terminate the Appium driver session."""
        if self._driver is not None:
            with contextlib.suppress(Exception):
                self._driver.quit()
            self._driver = None
