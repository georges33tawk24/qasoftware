"""The front door — `bureau run <url>`.

Nothing else in the suite touched argument wiring, and the first real invocation of
`bureau run` died in `RunConfig` on four validation errors at once: a viewport that was
still a string, and three repeatable flags defaulting to None instead of an empty list.
Every one is invisible to mypy and to every other test.
"""

from __future__ import annotations

import pytest

from engine.artifact.models import RunConfig, Viewport
from engine.cli import build_parser


def parse(*argv: str) -> object:
    return build_parser().parse_args(list(argv))


def test_run_builds_a_valid_config_from_bare_arguments() -> None:
    """The whole point: a URL and nothing else has to work."""
    args = parse("run", "https://example.test/")
    config = RunConfig(
        viewports=args.viewport or list(RunConfig().viewports),  # type: ignore[attr-defined]
        maxDepth=args.max_depth,  # type: ignore[attr-defined]
        maxPages=args.max_pages,  # type: ignore[attr-defined]
        include=args.include,  # type: ignore[attr-defined]
        exclude=args.exclude,  # type: ignore[attr-defined]
        maskSelectors=args.mask,  # type: ignore[attr-defined]
        consentSelectors=args.consent,  # type: ignore[attr-defined]
    )
    assert config.viewports
    assert config.include == [] and config.exclude == [] and config.maskSelectors == []


def test_a_named_viewport_arrives_as_a_viewport_not_a_string() -> None:
    args = parse("run", "https://example.test/", "--viewport", "desktop_1440")
    assert [type(v) for v in args.viewport] == [Viewport]  # type: ignore[attr-defined]


@pytest.mark.parametrize("flag", ["--include", "--exclude", "--mask", "--consent"])
def test_repeatable_flags_default_to_a_list(flag: str) -> None:
    """None here is four pydantic errors before the crawl starts."""
    args = parse("run", "https://example.test/")
    assert getattr(args, flag.lstrip("-").replace("-", "_")) == []


def test_run_and_capture_agree_on_their_shared_flags() -> None:
    """Two parsers describing the same crawl is how one of them drifts."""
    shared = ("include", "exclude", "mask", "consent", "viewport", "max_pages", "max_depth")
    run = parse("run", "https://example.test/")
    capture = parse("capture", "https://example.test/")
    for name in shared:
        if hasattr(capture, name):
            assert getattr(run, name) == getattr(capture, name), name
