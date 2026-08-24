"""Capture — SPEC §5. Produces the run artifact; never an issue."""

from engine.capture.driver import BrowserDriver, ContextOptions, get_driver
from engine.capture.run import CaptureResult, capture

__all__ = ["BrowserDriver", "CaptureResult", "ContextOptions", "capture", "get_driver"]
