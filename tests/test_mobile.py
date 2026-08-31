"""Unit and regression tests for Phase 11 — Mobile Native Apps (Appium & mitmproxy)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.artifact.context import RunContext
from engine.artifact.models import Box, PageArtifact, PageRecord, RunConfig, RunManifest, Viewport
from engine.artifact.store import RunPaths, write_page, write_run_manifest
from engine.capture.appium_driver import AppiumDriver, MobileAppConfig
from engine.capture.driver import DriverUnavailable
from engine.capture.layout import derive
from engine.capture.mitm import InterceptedFlow, MitmCapture, flow_to_network_entry
from engine.capture.mobile import (
    _parse_android_bounds,
    parse_android_hierarchy,
    parse_ios_hierarchy,
    parse_mobile_hierarchy,
)
from engine.checkers import runner

ANDROID_SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <android.widget.FrameLayout bounds="[0,0][1080,2400]"
    class="android.widget.FrameLayout" resource-id="com.example.app:id/root"
    package="com.example.app" content-desc="" displayed="true">
    <android.widget.LinearLayout bounds="[0,100][1080,2300]"
      class="android.widget.LinearLayout" displayed="true">
      <android.widget.TextView bounds="[48,150][1032,250]" text="Welcome to Mobile QA"
        class="android.widget.TextView" resource-id="com.example.app:id/welcome_title"
        content-desc="" displayed="true" />
      <android.widget.EditText bounds="[48,300][1032,450]" text="user@example.com"
        class="android.widget.EditText" resource-id="com.example.app:id/email_input"
        content-desc="Email address input" focusable="true" enabled="true" displayed="true" />
      <android.widget.ImageView bounds="[48,500][248,700]" class="android.widget.ImageView"
        resource-id="com.example.app:id/avatar_img" content-desc="User Profile Picture"
        displayed="true" />
      <android.widget.Button bounds="[48,800][1032,960]" text="Submit Order"
        class="android.widget.Button" resource-id="com.example.app:id/submit_button"
        clickable="true" enabled="true" displayed="true" />
      <android.widget.TextView bounds="[48,1000][200,1030]" text="Small link"
        class="android.widget.TextView" resource-id="com.example.app:id/tiny_link"
        clickable="true" displayed="true" />
    </android.widget.LinearLayout>
  </android.widget.FrameLayout>
</hierarchy>
"""

IOS_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<AppiumAUT>
  <XCUIElementTypeApplication type="XCUIElementTypeApplication" name="MobileApp"
    label="MobileApp" enabled="true" visible="true" x="0" y="0" width="393" height="852">
    <XCUIElementTypeNavigationBar type="XCUIElementTypeNavigationBar" name="Checkout"
      label="Checkout" enabled="true" visible="true" x="0" y="44" width="393" height="44" />
    <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" value="Order Total"
      label="Order Total" name="total_label" enabled="true" visible="true"
      x="20" y="120" width="353" height="30" />
    <XCUIElementTypeTextField type="XCUIElementTypeTextField" value="Promo Code"
      label="Enter discount promo code" name="promo_field" enabled="true" visible="true"
      x="20" y="170" width="353" height="44" />
    <XCUIElementTypeButton type="XCUIElementTypeButton" label="Complete Purchase"
      name="checkout_btn" enabled="true" visible="true" x="20" y="240" width="353" height="50" />
    <XCUIElementTypeImage type="XCUIElementTypeImage" name="product_hero"
      label="Product preview image" enabled="true" visible="true"
      x="20" y="310" width="100" height="100" />
  </XCUIElementTypeApplication>
</AppiumAUT>
"""


def test_android_bounds_parsing() -> None:
    box = _parse_android_bounds("[48,150][1032,250]")
    assert box.x == 48.0
    assert box.y == 150.0
    assert box.w == 984.0
    assert box.h == 100.0


def test_parse_android_hierarchy() -> None:
    elements = parse_android_hierarchy(ANDROID_SAMPLE_XML)
    assert len(elements) == 7

    # Root FrameLayout
    root = elements[0]
    assert root.tag == "div"
    assert root.testId == "root"
    assert root.box == Box(x=0.0, y=0.0, w=1080.0, h=2400.0)

    # Title TextView
    title = next(e for e in elements if e.testId == "welcome_title")
    assert title.tag == "p"
    assert title.text == "Welcome to Mobile QA"
    assert title.box == Box(x=48.0, y=150.0, w=984.0, h=100.0)

    # Email EditText
    email = next(e for e in elements if e.testId == "email_input")
    assert email.tag == "input"
    assert email.role == "textbox"
    assert email.field is not None
    assert email.field.placeholder == "user@example.com"
    assert email.field.labelledBy == "Email address input"

    # Avatar ImageView
    avatar = next(e for e in elements if e.testId == "avatar_img")
    assert avatar.tag == "img"
    assert avatar.image is not None
    assert avatar.image.alt == "User Profile Picture"

    # Submit Button
    btn = next(e for e in elements if e.testId == "submit_button")
    assert btn.tag == "button"
    assert btn.role == "button"
    assert btn.clickable is True
    assert btn.text == "Submit Order"


def test_parse_android_hierarchy_scaled() -> None:
    elements = parse_android_hierarchy(ANDROID_SAMPLE_XML, scale_factor=2.0)
    title = next(e for e in elements if e.testId == "welcome_title")
    assert title.box == Box(x=24.0, y=75.0, w=492.0, h=50.0)


def test_parse_ios_hierarchy() -> None:
    elements = parse_ios_hierarchy(IOS_SAMPLE_XML)
    assert len(elements) == 6

    # Navigation Bar
    nav = next(e for e in elements if e.tag == "nav")
    assert nav.role == "navigation"

    # Static Text
    total = next(e for e in elements if e.testId == "total_label")
    assert total.tag == "p"
    assert total.text == "Order Total"
    assert total.box == Box(x=20.0, y=120.0, w=353.0, h=30.0)

    # Text Field
    field = next(e for e in elements if e.testId == "promo_field")
    assert field.tag == "input"
    assert field.role == "textbox"
    assert field.field is not None
    assert field.field.labelledBy == "Enter discount promo code"

    # Button
    btn = next(e for e in elements if e.testId == "checkout_btn")
    assert btn.tag == "button"
    assert btn.clickable is True
    assert btn.text == "Complete Purchase"
    assert btn.box == Box(x=20.0, y=240.0, w=353.0, h=50.0)


def test_parse_mobile_hierarchy_auto_detect() -> None:
    android_res = parse_mobile_hierarchy(ANDROID_SAMPLE_XML)
    assert len(android_res) == 7
    assert any(e.testId == "submit_button" for e in android_res)

    ios_res = parse_mobile_hierarchy(IOS_SAMPLE_XML)
    assert len(ios_res) == 6
    assert any(e.testId == "checkout_btn" for e in ios_res)


def test_mitmproxy_flow_to_network_entry() -> None:
    flow = InterceptedFlow(
        url="https://api.example.com/v1/auth/login",
        method="POST",
        status=200,
        req_headers={"Content-Type": "application/json"},
        res_headers={"content-type": "application/json; charset=utf-8"},
        req_body='{"user":"test"}',
        res_body=b'{"token":"jwt_12345"}',
        start_time_ms=100.0,
        duration_ms=45.0,
        transfer_bytes=512,
    )
    entry = flow_to_network_entry(flow)
    assert entry.url == "https://api.example.com/v1/auth/login"
    assert entry.method == "POST"
    assert entry.status == 200
    assert entry.type == "fetch"
    assert entry.size.transferBytes == 512
    assert entry.timing.durationMs == 45.0
    assert entry.resBodyHash is not None


def test_mitm_capture_collector() -> None:
    mitm = MitmCapture()
    mitm.record_raw_dict(
        {
            "url": "https://api.example.com/v1/feed",
            "method": "GET",
            "status": 200,
            "res_headers": {"content-type": "application/json"},
            "transfer_bytes": 1024,
        }
    )
    mitm.record_raw_dict(
        {
            "url": "https://cdn.example.com/logo.png",
            "method": "GET",
            "status": 404,
            "res_headers": {"content-type": "image/png"},
            "transfer_bytes": 120,
        }
    )
    entries = mitm.export_entries()
    assert len(entries) == 2
    assert entries[0].type == "fetch"
    assert entries[1].type == "image"
    assert entries[1].status == 404


def test_mobile_artifact_checker_execution(tmp_path: Path) -> None:
    """Verify that the engine's checker suite runs seamlessly over mobile native artifacts."""
    run_dir = tmp_path / "run_mobile_test"
    paths = RunPaths(run_dir)

    manifest = RunManifest(
        runId="run_mobile_test",
        target="com.example.app",
        startedAt=datetime.now(UTC),
        config=RunConfig(platform="android"),
    )
    write_run_manifest(paths, manifest)

    pid = "p_main_screen"
    viewport = Viewport(name="mobile_android", width=1080, height=2400, deviceScaleFactor=2.0)

    page = PageRecord(
        id=pid,
        url="mobile://android/com.example.app",
        path="/",
        status=200,
    )
    artifact = PageArtifact(page=page)
    elements = parse_android_hierarchy(ANDROID_SAMPLE_XML)
    artifact.elements[viewport.name] = elements
    artifact.layout[viewport.name] = derive(pid, viewport.name, elements)
    artifact.dom = ANDROID_SAMPLE_XML

    # Attach network failure
    flow = InterceptedFlow(
        url="https://api.example.com/v1/config.json",
        method="GET",
        status=500,
        res_headers={"content-type": "application/json"},
        transfer_bytes=80,
    )
    artifact.network = [flow_to_network_entry(flow)]

    write_page(paths, artifact)

    # Run checkers
    ctx = RunContext.open(run_dir)
    result = runner.check(ctx)

    assert result.issues is not None
    # Verify subresource checker caught the failing API endpoint
    subresource_issues = [i for i in result.issues if i.checkerId == "free.subresource"]
    assert len(subresource_issues) >= 1
    assert any("/config.json" in i.title for i in subresource_issues)


def test_appium_driver_unavailable() -> None:
    config = MobileAppConfig(appium_url="http://127.0.0.1:49999")
    driver = AppiumDriver(config)
    with pytest.raises(DriverUnavailable) as exc_info:
        asyncio.run(driver.launch())
    assert "Appium" in str(exc_info.value)


def test_parse_invalid_xml_returns_empty() -> None:
    assert parse_android_hierarchy("invalid xml <<<>>>") == []
    assert parse_ios_hierarchy("not xml <<") == []


def test_capture_mobile_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.capture import appium_driver
    from engine.capture.run import capture_mobile

    class FakeAppiumDriver:
        def __init__(self, config: appium_driver.MobileAppConfig) -> None:
            self.config = config

        async def launch(self) -> None:
            pass

        async def get_page_source(self) -> str:
            return ANDROID_SAMPLE_XML

        async def get_screenshot(self) -> bytes:
            # 1x1 transparent PNG bytes
            return (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
                b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

        async def get_viewport(self) -> Viewport:
            return Viewport(name="mobile_android", width=1080, height=2400, deviceScaleFactor=2.0)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(appium_driver, "AppiumDriver", FakeAppiumDriver)

    config = RunConfig(
        platform="android",
        appPackage="com.example.app",
        appActivity=".MainActivity",
    )
    result = asyncio.run(capture_mobile(tmp_path, config=config))
    assert result.manifest.target == "com.example.app"
    assert len(result.manifest.pageIds) == 1
    page_dir = result.paths.root / "pages" / "p_main_screen" / "viewports" / "mobile_android"
    assert (page_dir / "elements.json").exists()
    assert (page_dir / "full.png").exists()
