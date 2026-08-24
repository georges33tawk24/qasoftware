"""Colour maths — CIELAB and CIEDE2000.

CLAUDE.md: colour comparison is ΔE, never raw RGB distance. Two colours 30 RGB units
apart can be indistinguishable in one part of the space and obviously different in
another, and a tool that reports the wrong one loses its credibility fast.
"""

from __future__ import annotations

import re
from math import atan2, cos, degrees, exp, hypot, radians, sin, sqrt

DEFAULT_DELTA_E = 2.0
"""SPEC §7: the default tolerance. Roughly "a careful eye can just tell"."""

Rgba = tuple[float, float, float, float]
Lab = tuple[float, float, float]

_RGB = re.compile(r"^rgba?\(([^)]+)\)$")
_HEX = re.compile(r"^#([0-9a-fA-F]{3,8})$")


def parse(value: str) -> Rgba | None:
    """`rgb(17, 17, 17)`, `rgba(0, 0, 0, 0)` and `#1db954` — what browsers and design
    tools actually emit."""
    value = value.strip()
    match = _RGB.match(value)
    if match:
        parts = [float(p) for p in re.split(r"[\s,/]+", match.group(1).strip()) if p]
        if len(parts) < 3:
            return None
        return parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 1.0
    match = _HEX.match(value)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        if len(digits) not in (6, 8):
            return None
        channels = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
        alpha = channels[3] / 255 if len(channels) == 4 else 1.0
        return channels[0], channels[1], channels[2], alpha
    return None


def to_hex(colour: Rgba) -> str:
    return "#" + "".join(f"{round(max(0.0, min(255.0, c))):02x}" for c in colour[:3])


def over(top: Rgba, bottom: Rgba) -> Rgba:
    """Source-over compositing, so a translucent colour is compared as it looks."""
    alpha = top[3] + bottom[3] * (1 - top[3])
    if alpha == 0:
        return 0.0, 0.0, 0.0, 0.0

    def mix(t: float, b: float) -> float:
        return (t * top[3] + b * bottom[3] * (1 - top[3])) / alpha

    return mix(top[0], bottom[0]), mix(top[1], bottom[1]), mix(top[2], bottom[2]), alpha


def to_lab(colour: Rgba) -> Lab:
    """sRGB to CIELAB, D65."""

    def linear(channel: float) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = linear(colour[0]), linear(colour[1]), linear(colour[2])
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(first: Lab, second: Lab) -> float:
    """CIEDE2000."""
    l1, a1, b1 = first
    l2, a2, b2 = second
    c1, c2 = hypot(a1, b1), hypot(a2, b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - sqrt(c_bar**7 / (c_bar**7 + 25**7))) if c_bar else 0.5
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = hypot(a1p, b1), hypot(a2p, b2)
    h1p = degrees(atan2(b1, a1p)) % 360 if (b1 or a1p) else 0.0
    h2p = degrees(atan2(b2, a2p)) % 360 if (b2 or a2p) else 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        dhp -= 360 if dhp > 180 else 0
        dhp += 360 if dhp < -180 else 0
    dhp_big = 2 * sqrt(c1p * c2p) * sin(radians(dhp) / 2)

    lbp = (l1 + l2) / 2
    cbp = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) > 180:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    else:
        hbp = (h1p + h2p) / 2

    t = (
        1
        - 0.17 * cos(radians(hbp - 30))
        + 0.24 * cos(radians(2 * hbp))
        + 0.32 * cos(radians(3 * hbp + 6))
        - 0.20 * cos(radians(4 * hbp - 63))
    )
    s_l = 1 + (0.015 * (lbp - 50) ** 2) / sqrt(20 + (lbp - 50) ** 2)
    s_c = 1 + 0.045 * cbp
    s_h = 1 + 0.015 * cbp * t
    r_t = -sin(radians(2 * (30 * exp(-(((hbp - 275) / 25) ** 2))))) * (
        2 * sqrt(cbp**7 / (cbp**7 + 25**7)) if cbp else 0.0
    )
    return sqrt(
        (dlp / s_l) ** 2
        + (dcp / s_c) ** 2
        + (dhp_big / s_h) ** 2
        + r_t * (dcp / s_c) * (dhp_big / s_h)
    )


def distance(first: str, second: str) -> float | None:
    """ΔE between two CSS colours, or None if either will not parse."""
    a, b = parse(first), parse(second)
    if a is None or b is None:
        return None
    white: Rgba = (255.0, 255.0, 255.0, 1.0)
    return delta_e(to_lab(over(a, white)), to_lab(over(b, white)))


def nearest(colour: str, palette: list[str]) -> tuple[str, float] | None:
    """The closest palette entry and its ΔE, so a finding can name the token it missed."""
    best: tuple[str, float] | None = None
    for candidate in palette:
        gap = distance(colour, candidate)
        if gap is not None and (best is None or gap < best[1]):
            best = (candidate, gap)
    return best
