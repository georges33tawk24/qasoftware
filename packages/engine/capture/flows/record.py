"""Record-a-flow — SPEC §15.

`playwright codegen` wrapped: the user clicks a journey once and it becomes a named
regression test that runs on every future run.

What is stored is a **list of steps**, not the generated Python. Two reasons, and both
matter more than the convenience of `exec`:

- CLAUDE.md is explicit that every action goes through the step wrapper, so reproduction
  instructions fall out of the log. Running a generated script bypasses that and the
  failure arrives with no steps.
- A recorded script is code from a browser session. Executing it later, on a schedule,
  is the kind of thing that is fine until it very much is not.

Passwords never land in a recording. A field the recorder saw as a password becomes a
reference to the persona, resolved at replay from the environment or the keychain.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.capture.flows.runner import FlowSpec
from engine.capture.flows.steps import Flow

CODEGEN_TIMEOUT = 30 * 60
"""Half an hour to click through a journey. Past that the window was left open."""

SECRET_FIELD = re.compile(r"pass|secret|token|otp|cvv|card", re.IGNORECASE)
PERSONA_PASSWORD = "persona:password"
PERSONA_USER = "persona:user"

ACTIONS = ("goto", "click", "fill", "press", "check", "select", "expect_visible", "expect_text")


@dataclass
class RecordedStep:
    action: str
    selector: str = ""
    value: str = ""
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "selector": self.selector,
            "value": self.value,
            "description": self.description,
        }


@dataclass
class Recording:
    name: str
    steps: list[RecordedStep] = field(default_factory=list)
    script: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": [step.as_dict() for step in self.steps],
            "script": self.script,
        }


# ------------------------------------------------------------------ the recorder


def available() -> bool:
    """Codegen needs a real display; a headless server has none, and should say so."""
    return shutil.which("playwright") is not None


async def codegen(url: str, out: Path, *, timeout: int = CODEGEN_TIMEOUT) -> str:
    """Open the recorder and hand back the script the user's clicks produced."""
    if not available():
        raise RuntimeError("playwright is not on PATH; record a flow from a machine with a display")
    out.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        "playwright",
        "codegen",
        "--target",
        "python-async",
        "-o",
        str(out),
        url,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        process.kill()
        raise RuntimeError("the recorder was left open; nothing was saved") from None
    return out.read_text() if out.is_file() else ""


# -------------------------------------------------------------------- the parser

_CALL = re.compile(r"await\s+(?:page|frame)\.(?P<chain>.+?)\s*$")
_STRING = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
_GET_BY = re.compile(
    rf"get_by_(?P<what>role|label|placeholder|text|title|test_id)\(\s*{_STRING}"
    rf"(?:\s*,\s*name\s*=\s*{_STRING})?",
)
_LOCATOR = re.compile(rf"locator\(\s*{_STRING}")
_TAIL = re.compile(
    rf"(?:^|\.)(?P<verb>click|fill|press|check|select_option|goto)\(\s*(?:{_STRING})?"
)
_EXPECT = re.compile(
    rf"expect\((?P<inner>.+)\)\.(?P<matcher>to_be_visible|to_have_text|to_contain_text)\(\s*(?:{_STRING})?"
)


def _first(match: re.Match[str], *groups: int) -> str:
    """The first of these groups that matched. Quoting is either style, so every string
    in these patterns contributes two alternatives and only one of them is ever set."""
    for group in groups:
        if group > match.re.groups:
            continue
        value = match.group(group)
        if value:
            return value.encode().decode("unicode_escape")
    return ""


def _selector(chain: str) -> str:
    """Playwright's own selector engines, so the step wrapper can use them unchanged."""
    if found := _GET_BY.search(chain):
        what = found.group("what")
        first = _first(found, 2, 3)
        name = _first(found, 4, 5)
        if what == "role":
            return f'role={first}[name="{name}"]' if name else f"role={first}"
        if what == "test_id":
            return f"data-testid={first}"
        return f"{what}={first}"
    if found := _LOCATOR.search(chain):
        return _first(found, 1, 2)
    return ""


def parse(script: str) -> list[RecordedStep]:
    """Codegen's output as neutral steps. Anything unrecognised is dropped, not guessed."""
    steps: list[RecordedStep] = []
    for raw in script.splitlines():
        line = raw.strip()
        if line.startswith("await expect("):
            step = _expectation(line)
        else:
            found = _CALL.match(line)
            step = _action(found.group("chain")) if found else None
        if step is not None:
            steps.append(step)
    return steps


def _action(chain: str) -> RecordedStep | None:
    tail = _TAIL.search(chain)
    if tail is None:
        return None
    verb = tail.group("verb")
    argument = _first(tail, 2, 3)
    if verb == "goto":
        return RecordedStep("goto", value=argument, description=f"Open {argument}")
    selector = _selector(chain)
    if not selector:
        return None
    if verb == "fill":
        secret = bool(SECRET_FIELD.search(selector))
        return RecordedStep(
            "fill",
            selector=selector,
            # A recorded password is a stored password. The persona supplies it at replay.
            value=PERSONA_PASSWORD if secret else argument,
            description=f"Fill in {_label(selector)}",
        )
    if verb == "select_option":
        return RecordedStep(
            "select", selector, argument, f"Choose {argument} in {_label(selector)}"
        )
    if verb == "press":
        return RecordedStep("press", selector, argument, f"Press {argument} in {_label(selector)}")
    if verb == "check":
        return RecordedStep("check", selector, description=f"Tick {_label(selector)}")
    return RecordedStep("click", selector, description=f"Click {_label(selector)}")


def _expectation(line: str) -> RecordedStep | None:
    found = _EXPECT.search(line)
    if found is None:
        return None
    matcher = found.group("matcher")
    text = _first(found, 3, 4)
    if matcher == "to_be_visible":
        selector = _selector(found.group("inner"))
        if not selector:
            return None
        return RecordedStep("expect_visible", selector, description=f"See {_label(selector)}")
    return RecordedStep("expect_text", value=text, description=f"See the text “{text}”")


def _label(selector: str) -> str:
    """A selector a person recognises: the name they clicked, not the CSS."""
    if match := re.search(r'name="([^"]+)"', selector):
        return f"“{match.group(1)}”"
    if "=" in selector and not selector.startswith((".", "#", "[")):
        return f"“{selector.split('=', 1)[1]}”"
    return f"`{selector}`"


# -------------------------------------------------------------------- the replay


def to_spec(
    name: str,
    steps: list[RecordedStep],
    *,
    secrets: dict[str, str] | None = None,
    kind: str = "recorded",
) -> FlowSpec:
    """A recorded journey as a flow the runner already knows how to execute and retry."""
    resolved = dict(secrets or {})

    async def body(flow: Flow) -> None:
        for step in steps:
            await _perform(flow, step, resolved)

    return FlowSpec(name=name, kind=kind, body=body, data={"recorded": True})


async def _perform(flow: Flow, step: RecordedStep, secrets: dict[str, str]) -> None:
    value = step.value
    if value == PERSONA_PASSWORD:
        value = secrets.get("password", "")
    elif value == PERSONA_USER:
        value = secrets.get("user", "")

    actions: dict[str, Callable[[], Awaitable[Any]]] = {
        "goto": lambda: flow.goto(value),
        "click": lambda: flow.click(step.selector, described=step.description),
        "fill": lambda: flow.fill(step.selector, value, described=step.description),
        "press": lambda: flow.press(step.selector, value),
        "check": lambda: flow.click(step.selector, described=step.description),
        "select": lambda: flow.fill(step.selector, value, described=step.description),
        "expect_visible": lambda: flow.expect_visible(
            step.selector,
            step.description,
            kind="recorded-step-missing",
            message=f"{step.description} did not happen",
        ),
        "expect_text": lambda: flow.expect_text(
            value,
            step.description,
            kind="recorded-text-missing",
            message=f"{step.description} did not happen",
        ),
    }
    action = actions.get(step.action)
    if action is not None:
        await action()
