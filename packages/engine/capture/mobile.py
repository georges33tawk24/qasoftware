"""Mobile native accessibility hierarchy parsers and element mapping — SPEC §19 (Phase 11).

Converts native Android (UiAutomator2) and iOS (XCUITest) accessibility trees into standard
`ElementRecord` models so all 58 layout, typography, content, a11y, and Figma checkers
run seamlessly across mobile native applications.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Literal

from engine.artifact.models import Box, ElementRecord, ElementStyles, FieldInfo, ImageInfo
from engine.issues.fingerprint import element_stable_key

_ANDROID_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

_ANDROID_TAG_MAP: dict[str, tuple[str, str | None]] = {
    "android.widget.Button": ("button", "button"),
    "android.widget.ImageButton": ("button", "button"),
    "android.widget.TextView": ("p", None),
    "android.widget.ImageView": ("img", "img"),
    "android.widget.EditText": ("input", "textbox"),
    "android.widget.CheckBox": ("input", "checkbox"),
    "android.widget.RadioButton": ("input", "radio"),
    "android.widget.Switch": ("input", "switch"),
    "android.widget.ToggleButton": ("button", "button"),
    "android.widget.ProgressBar": ("div", "progressbar"),
    "android.widget.SeekBar": ("input", "slider"),
    "android.widget.ScrollView": ("div", "region"),
    "android.widget.HorizontalScrollView": ("div", "region"),
    "android.widget.ListView": ("ul", "list"),
    "androidx.recyclerview.widget.RecyclerView": ("ul", "list"),
    "android.widget.FrameLayout": ("div", None),
    "android.widget.LinearLayout": ("div", None),
    "android.widget.RelativeLayout": ("div", None),
    "androidx.compose.ui.platform.ComposeView": ("div", None),
}

_IOS_TAG_MAP: dict[str, tuple[str, str | None]] = {
    "XCUIElementTypeButton": ("button", "button"),
    "XCUIElementTypeStaticText": ("p", None),
    "XCUIElementTypeImage": ("img", "img"),
    "XCUIElementTypeTextField": ("input", "textbox"),
    "XCUIElementTypeSecureTextField": ("input", "textbox"),
    "XCUIElementTypeTextView": ("textarea", "textbox"),
    "XCUIElementTypeSwitch": ("input", "switch"),
    "XCUIElementTypeSlider": ("input", "slider"),
    "XCUIElementTypeProgressIndicator": ("div", "progressbar"),
    "XCUIElementTypeTable": ("ul", "list"),
    "XCUIElementTypeCell": ("li", "listitem"),
    "XCUIElementTypeCollectionView": ("div", "grid"),
    "XCUIElementTypeScrollView": ("div", "region"),
    "XCUIElementTypeNavigationBar": ("nav", "navigation"),
    "XCUIElementTypeTabBar": ("nav", "tablist"),
    "XCUIElementTypeLink": ("a", "link"),
    "XCUIElementTypeOther": ("div", None),
}


def _parse_android_bounds(raw: str) -> Box:
    """`[x1,y1][x2,y2]` -> Box(x=x1, y=y1, w=x2-x1, h=y2-y1)."""
    match = _ANDROID_BOUNDS.match(raw.strip())
    if not match:
        return Box(x=0.0, y=0.0, w=0.0, h=0.0)
    x1, y1, x2, y2 = (float(v) for v in match.groups())
    return Box(x=x1, y=y1, w=max(0.0, x2 - x1), h=max(0.0, y2 - y1))


def _clean_resource_id(raw: str | None) -> str | None:
    """`com.example.app:id/submit_button` -> `submit_button`."""
    if not raw:
        return None
    if ":id/" in raw:
        return raw.split(":id/", 1)[1]
    return raw


def parse_android_hierarchy(
    xml_content: str, *, scale_factor: float = 1.0, max_elements: int = 5000
) -> list[ElementRecord]:
    """Parse Android UiAutomator2 XML hierarchy into standard ElementRecords."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    elements: list[ElementRecord] = []
    counter = 0

    def _walk(node: ET.Element, parent_id: str | None) -> str | None:
        nonlocal counter
        if len(elements) >= max_elements:
            return None

        counter += 1
        el_id = f"el_{counter:04d}"
        cls_name = node.attrib.get("class", node.tag)
        tag, role = _ANDROID_TAG_MAP.get(cls_name, ("div", None))

        bounds_raw = node.attrib.get("bounds", "[0,0][0,0]")
        box = _parse_android_bounds(bounds_raw)
        if scale_factor != 1.0 and scale_factor > 0:
            box = Box(
                x=round(box.x / scale_factor, 2),
                y=round(box.y / scale_factor, 2),
                w=round(box.w / scale_factor, 2),
                h=round(box.h / scale_factor, 2),
            )

        text = node.attrib.get("text", "").strip()
        content_desc = node.attrib.get("content-desc", "").strip()
        display_text = text or content_desc
        resource_id = node.attrib.get("resource-id", "")
        test_id = _clean_resource_id(resource_id)

        clickable = node.attrib.get("clickable") == "true" or tag == "button"
        focusable = node.attrib.get("focusable") == "true"
        enabled = node.attrib.get("enabled", "true") == "true"
        displayed = node.attrib.get("displayed", "true") == "true"
        visible = displayed and box.w > 0 and box.h > 0

        # Build clean CSS-like selector for reports
        short_class = cls_name.split(".")[-1].lower()
        if test_id:
            selector = f"#{test_id}"
        elif content_desc:
            selector = f"{tag}[aria-label='{content_desc[:20]}']"
        else:
            selector = f"{tag}.{short_class}"

        field = None
        if tag == "input":
            field = FieldInfo(
                type="text" if role == "textbox" else (role or "text"),
                name=test_id or "",
                required=False,
                disabled=not enabled,
                readOnly=False,
                placeholder=text or None,
                labelledBy=content_desc or None,
            )

        image = None
        if tag == "img":
            image = ImageInfo(
                src=test_id or f"android://{cls_name}",
                naturalW=int(box.w),
                naturalH=int(box.h),
                renderedW=box.w,
                renderedH=box.h,
                alt=content_desc or None,
            )

        styles = ElementStyles(
            color="rgb(0, 0, 0)",
            backgroundColor="transparent",
            fontFamily="Roboto, sans-serif",
            fontSize=16.0,
            fontWeight=400,
            lineHeight=20.0,
            paddingLeft=0.0,
            paddingRight=0.0,
            paddingTop=0.0,
            paddingBottom=0.0,
            marginLeft=0.0,
            marginRight=0.0,
            marginTop=0.0,
            marginBottom=0.0,
            position="relative",
            overflow="visible",
        )

        record = ElementRecord(
            id=el_id,
            parentId=parent_id,
            childIds=[],
            stableKey="",
            selector=selector,
            tag=tag,
            role=role,
            classes=[cls_name, short_class] if cls_name else [],
            htmlId=resource_id or None,
            testId=test_id,
            text=display_text[:400],
            textLength=len(display_text),
            textFull=display_text[:400],
            box=box,
            boxViewport=box,
            scrollW=box.w,
            scrollH=box.h,
            visible=visible,
            clickable=clickable,
            focusable=focusable,
            styles=styles,
            resolvedBackground="rgb(255, 255, 255)",
            field=field,
            image=image,
        )
        record.stableKey = element_stable_key(record)
        elements.append(record)

        child_ids: list[str] = []
        for child_node in node:
            cid = _walk(child_node, el_id)
            if cid is not None:
                child_ids.append(cid)
        record.childIds = child_ids

        return el_id

    # The top-level hierarchy node
    if root.tag == "hierarchy":
        for child in root:
            _walk(child, None)
    else:
        _walk(root, None)

    return elements


def parse_ios_hierarchy(
    xml_content: str, *, scale_factor: float = 1.0, max_elements: int = 5000
) -> list[ElementRecord]:
    """Parse iOS XCUITest XML hierarchy into standard ElementRecords."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    elements: list[ElementRecord] = []
    counter = 0

    def _walk(node: ET.Element, parent_id: str | None) -> str | None:
        nonlocal counter
        if len(elements) >= max_elements:
            return None

        counter += 1
        el_id = f"el_{counter:04d}"
        elem_type = node.attrib.get("type", node.tag)
        tag, role = _IOS_TAG_MAP.get(elem_type, ("div", None))

        try:
            x = float(node.attrib.get("x", "0"))
            y = float(node.attrib.get("y", "0"))
            w = float(node.attrib.get("width", "0"))
            h = float(node.attrib.get("height", "0"))
        except ValueError:
            x, y, w, h = 0.0, 0.0, 0.0, 0.0

        if scale_factor != 1.0 and scale_factor > 0:
            box = Box(
                x=round(x / scale_factor, 2),
                y=round(y / scale_factor, 2),
                w=round(w / scale_factor, 2),
                h=round(h / scale_factor, 2),
            )
        else:
            box = Box(x=x, y=y, w=w, h=h)

        name = node.attrib.get("name", "").strip()
        label = node.attrib.get("label", "").strip()
        value = node.attrib.get("value", "").strip()
        display_text = label or value or name

        enabled = node.attrib.get("enabled", "true") == "true"
        visible_attr = node.attrib.get("visible", "true") == "true"
        visible = visible_attr and box.w > 0 and box.h > 0
        clickable = tag in ("button", "a") or elem_type in (
            "XCUIElementTypeButton",
            "XCUIElementTypeCell",
        )
        focusable = enabled and clickable

        short_type = elem_type.replace("XCUIElementType", "").lower()
        if name:
            selector = f"#{name}"
        elif label:
            selector = f"{tag}[aria-label='{label[:20]}']"
        else:
            selector = f"{tag}.{short_type}"

        field = None
        if tag in ("input", "textarea"):
            field = FieldInfo(
                type="text" if role == "textbox" else (role or "text"),
                name=name or "",
                required=False,
                disabled=not enabled,
                readOnly=False,
                placeholder=label or None,
                labelledBy=label or None,
            )

        image = None
        if tag == "img":
            image = ImageInfo(
                src=name or f"ios://{elem_type}",
                naturalW=int(box.w),
                naturalH=int(box.h),
                renderedW=box.w,
                renderedH=box.h,
                alt=label or None,
            )

        styles = ElementStyles(
            color="rgb(0, 0, 0)",
            backgroundColor="transparent",
            fontFamily="-apple-system, system-ui, sans-serif",
            fontSize=17.0,
            fontWeight=400,
            lineHeight=22.0,
            paddingLeft=0.0,
            paddingRight=0.0,
            paddingTop=0.0,
            paddingBottom=0.0,
            marginLeft=0.0,
            marginRight=0.0,
            marginTop=0.0,
            marginBottom=0.0,
            position="relative",
            overflow="visible",
        )

        record = ElementRecord(
            id=el_id,
            parentId=parent_id,
            childIds=[],
            stableKey="",
            selector=selector,
            tag=tag,
            role=role,
            classes=[elem_type, short_type] if elem_type else [],
            htmlId=name or None,
            testId=name or None,
            text=display_text[:400],
            textLength=len(display_text),
            textFull=display_text[:400],
            box=box,
            boxViewport=box,
            scrollW=box.w,
            scrollH=box.h,
            visible=visible,
            clickable=clickable,
            focusable=focusable,
            styles=styles,
            resolvedBackground="rgb(255, 255, 255)",
            field=field,
            image=image,
        )
        record.stableKey = element_stable_key(record)
        elements.append(record)

        child_ids: list[str] = []
        for child_node in node:
            cid = _walk(child_node, el_id)
            if cid is not None:
                child_ids.append(cid)
        record.childIds = child_ids

        return el_id

    if root.tag == "AppiumAUT":
        for child in root:
            _walk(child, None)
    else:
        _walk(root, None)
    return elements


def parse_mobile_hierarchy(
    xml_content: str,
    platform: Literal["android", "ios"] = "android",
    *,
    scale_factor: float = 1.0,
    max_elements: int = 5000,
) -> list[ElementRecord]:
    """Universal mobile hierarchy entrypoint."""
    if platform == "ios" or "<XCUIElementType" in xml_content or "<AppiumAUT" in xml_content:
        return parse_ios_hierarchy(
            xml_content, scale_factor=scale_factor, max_elements=max_elements
        )
    return parse_android_hierarchy(
        xml_content, scale_factor=scale_factor, max_elements=max_elements
    )
