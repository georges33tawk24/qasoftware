"""Group D, spelling — SPEC §8.4 D.

Spell checking is the classic false-positive machine, so the guards matter more than the
dictionary: lowercase words only (proper nouns are almost always capitalised), nothing
short, nothing that appears repeatedly across the site (that is the client's vocabulary,
not a typo), and nothing in the project dictionary.

Every false positive belongs in `RunConfig.dictionary`, never in a loosened rule.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import ElementRecord
from engine.checkers.base import checker
from engine.checkers.support import Surface, element_finding, widest_surfaces
from engine.issues.models import Category, Finding, Severity

WORD = re.compile(r"[a-zA-Z][a-zA-Z']{3,}")
MIN_LENGTH = 5
SITE_VOCABULARY_USES = 3
MONOSPACE = ("mono", "consol", "courier", "menlo", "code")


@checker
class Spelling:
    id = "content.spelling"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        try:
            from spellchecker import SpellChecker
        except ImportError:  # pragma: no cover - the dependency is declared
            return

        pages = widest_surfaces(ctx)
        counts: Counter[str] = Counter()
        candidates: list[tuple[Surface, ElementRecord, str]] = []
        for surface in pages:
            for element in surface.laid_out:
                if _is_code(element.styles.fontFamily):
                    continue
                for word in _words(element.text):
                    counts[word] += 1
                    candidates.append((surface, element, word))

        allowed = {w.lower() for w in ctx.manifest.config.dictionary}
        vocabulary = {w for w, n in counts.items() if n >= SITE_VOCABULARY_USES}
        checker_ = SpellChecker()
        unique = {w for _, _, w in candidates} - allowed - vocabulary
        unknown = checker_.unknown(unique) if unique else set()

        reported: set[str] = set()
        for surface, element, word in candidates:
            if word not in unknown or word in reported:
                continue
            reported.add(word)
            correction = checker_.correction(word)
            yield element_finding(
                self,
                surface,
                element,
                kind="possible-misspelling",
                title=f"{word!r} is not a word this checker knows",
                description="Add it to the project dictionary if it is deliberate.",
                expected=correction or "a known word",
                actual=word,
                data={"word": word, "suggestion": correction},
            )


def _words(text: str) -> Iterable[str]:
    for match in WORD.finditer(text):
        word = match.group(0).strip("'")
        if len(word) >= MIN_LENGTH and word.islower():
            yield word


def _is_code(family: str) -> bool:
    lowered = family.lower()
    return any(marker in lowered for marker in MONOSPACE)
