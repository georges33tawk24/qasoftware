"""Form discovery — a pure function over the artifact.

The battery in SPEC §8.4 H needs to know what a form *is* before it can exercise it. That
comes from `elements.json`, which already records every field's contract, so discovery
needs no browser and can be unit-tested against a frozen fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.artifact.context import RunContext
from engine.artifact.models import ElementRecord, FieldInfo, PageRecord
from engine.checkers.support import Surface, surfaces

TEXTUAL = frozenset({"text", "email", "url", "tel", "search", "password", "textarea", "number"})
IGNORED = frozenset({"submit", "button", "reset", "image", "hidden"})
SEARCH_NAMES = frozenset({"q", "query", "s", "search", "keyword", "term"})


@dataclass(frozen=True)
class Field:
    element: ElementRecord
    info: FieldInfo

    @property
    def selector(self) -> str:
        """The most durable handle we have, in that order."""
        if self.element.htmlId:
            return f"#{self.element.htmlId}"
        if self.element.testId:
            return f"[data-testid='{self.element.testId}']"
        if self.info.name:
            return f"[name='{self.info.name}']"
        return self.element.selector

    @property
    def label(self) -> str:
        return self.info.labelledBy or self.info.placeholder or self.info.name or self.info.type


@dataclass
class Form:
    pageId: str
    pagePath: str
    url: str
    viewport: str
    element: ElementRecord
    fields: list[Field] = field(default_factory=list)
    submit: str | None = None
    kind: str = "generic"

    @property
    def selector(self) -> str:
        if self.element.htmlId:
            return f"#{self.element.htmlId}"
        return self.element.selector

    @property
    def required(self) -> list[Field]:
        return [f for f in self.fields if f.info.required]

    @property
    def textual(self) -> list[Field]:
        return [f for f in self.fields if f.info.type in TEXTUAL]

    def by_type(self, kind: str) -> Field | None:
        return next((f for f in self.fields if f.info.type == kind), None)


def classify(fields: list[Field]) -> str:
    types = [f.info.type for f in fields]
    names = {(f.info.name or "").casefold() for f in fields}
    passwords = types.count("password")
    if passwords >= 2 or (passwords == 1 and {"confirm", "confirm_password"} & names):
        return "signup"
    if passwords == 1:
        return "login"
    if len(fields) == 1 and names & SEARCH_NAMES:
        return "search"
    return "generic"


def forms_on(surface: Surface) -> list[Form]:
    index = surface.by_id
    found: list[Form] = []
    for element in surface.elements:
        if element.form is None:
            continue
        form = Form(
            pageId=surface.page.id,
            pagePath=surface.page.path,
            url=surface.page.url,
            viewport=surface.viewport.name,
            element=element,
        )
        for candidate in surface.elements:
            info = candidate.field
            if info is None or info.disabled:
                continue
            owner = info.formElementId or _nearest_form(candidate, index)
            if owner != element.id:
                continue
            if info.type in ("submit", "button", "image"):
                if form.submit is None:
                    form.submit = Field(candidate, info).selector
                continue
            if info.type in IGNORED:
                continue
            form.fields.append(Field(candidate, info))
        if form.submit is None:
            form.submit = _submit_button(element, surface)
        if form.fields:
            form.kind = classify(form.fields)
            found.append(form)
    return found


def _nearest_form(element: ElementRecord, index: dict[str, ElementRecord]) -> str | None:
    current = index.get(element.parentId or "")
    depth = 0
    while current is not None and depth < 8:
        if current.form is not None:
            return current.id
        current = index.get(current.parentId or "")
        depth += 1
    return None


def _submit_button(form: ElementRecord, surface: Surface) -> str | None:
    """A `<button>` with no explicit type still submits its form."""
    index = surface.by_id
    for element in surface.elements:
        if element.tag != "button" or not element.clickable:
            continue
        if _nearest_form(element, index) != form.id:
            continue
        return Field(element, element.field or FieldInfo(type="submit")).selector
    return None


def discover(ctx: RunContext) -> list[Form]:
    """One form per page, at the widest viewport: the same form at three widths is one
    form, and exercising it three times is three times the noise."""
    seen: set[tuple[str, str]] = set()
    found: list[Form] = []
    widest: dict[str, Surface] = {}
    for surface in surfaces(ctx):
        current = widest.get(surface.page.id)
        if current is None or surface.viewport.width > current.viewport.width:
            widest[surface.page.id] = surface
    for surface in (widest[key] for key in sorted(widest)):
        for form in forms_on(surface):
            key = (form.pageId, form.selector)
            if key in seen:
                continue
            seen.add(key)
            found.append(form)
    return found


def login_form(forms: list[Form]) -> Form | None:
    return next((form for form in forms if form.kind == "login"), None)


def pages_with(ctx: RunContext, needles: tuple[str, ...]) -> list[PageRecord]:
    from engine.checkers.support import live_pages

    return [
        page
        for page in live_pages(ctx)
        if any(needle in page.path.casefold() for needle in needles)
    ]
