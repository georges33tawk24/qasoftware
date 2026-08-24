"""Crawl policy — SPEC §5. No browser: this is all URL arithmetic."""

from __future__ import annotations

from engine.capture.crawler import Frontier, UrlPolicy, normalise, path_shape

SEED = "https://example.test/"


def policy(**over: object) -> UrlPolicy:
    return UrlPolicy(SEED, **over)  # type: ignore[arg-type]


def test_normalise_is_the_dedupe_key() -> None:
    assert normalise("https://Example.test/Products/") == "https://example.test/Products"
    assert normalise("https://example.test/a?b=2&a=1#frag") == "https://example.test/a?a=1&b=2"
    assert normalise("https://example.test") == "https://example.test/"


def test_ignored_query_params_collapse_duplicates() -> None:
    ignore = frozenset({"utm_source", "ref"})
    a = normalise("https://example.test/p?utm_source=x&id=7", ignore)
    b = normalise("https://example.test/p?id=7&ref=y", ignore)
    assert a == b == "https://example.test/p?id=7"


def test_off_origin_is_refused_by_default() -> None:
    assert policy().allowed("https://other.test/x") == "off-origin"
    assert policy(same_origin=False).allowed("https://other.test/x") is None


def test_non_http_schemes_are_refused() -> None:
    assert policy().allowed("mailto:hi@example.test") == "scheme 'mailto'"
    assert policy().allowed("javascript:void(0)") == "scheme 'javascript'"


def test_include_and_exclude_patterns() -> None:
    assert policy(exclude=[r"/admin"]).allowed(f"{SEED}admin/users") == "matches an exclude pattern"
    only_blog = policy(include=[r"/blog/"])
    assert only_blog.allowed(f"{SEED}blog/post") is None
    assert only_blog.allowed(f"{SEED}shop") == "matches no include pattern"


def test_path_shape_collapses_ids_and_slugs() -> None:
    assert path_shape("https://x.test/blog/my-long-post-title") == "/blog/*"
    assert path_shape("https://x.test/orders/10482") == "/orders/*"
    assert path_shape("https://x.test/u/9f8a7b6c5d4e") == "/u/*"
    assert path_shape("https://x.test/about") == "/about"


def test_frontier_dedupes_and_respects_depth() -> None:
    frontier = Frontier(policy(), max_depth=1, max_pages=10)
    assert frontier.push(SEED, 0, None)
    assert not frontier.push(f"{SEED}?utm=1#x".replace("?utm=1", ""), 0, None)
    assert not frontier.push(f"{SEED}deep", 2, SEED)
    assert "deeper than maxDepth=1" in frontier.skipped["https://example.test/deep"]


def test_frontier_samples_templated_pages() -> None:
    frontier = Frontier(policy(), max_depth=3, max_pages=100, template_sample=2)
    accepted = [frontier.push(f"{SEED}blog/post-number-{i}", 1, SEED) for i in range(5)]
    assert accepted == [True, True, False, False, False]
    assert (
        "templated page /blog/* already sampled 2x"
        in frontier.skipped["https://example.test/blog/post-number-4"]
    )


def test_frontier_stops_at_max_pages() -> None:
    frontier = Frontier(policy(), max_depth=3, max_pages=2)
    for i in range(5):
        frontier.push(f"{SEED}page{i}", 1, SEED)
    assert [frontier.pop(), frontier.pop()] != [None, None]
    assert frontier.pop() is None
