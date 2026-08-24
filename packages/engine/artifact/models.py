"""The run artifact schema — SPEC §4.

This is the contract everything else depends on. Field names are camelCase and the
models use camelCase attributes deliberately: `model_dump()` is the on-disk shape, so
there is no alias flag to forget on a round-trip.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

Quad = Annotated[list[float], Field(min_length=4, max_length=4)]
"""top, right, bottom, left — CSS order."""


class ArtifactModel(BaseModel):
    """Strict base. Unknown fields are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- run.json


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"
    aborted = "aborted"


class Viewport(ArtifactModel):
    name: str
    width: int
    height: int
    deviceScaleFactor: float = 1.0


VIEWPORT_PRESETS = {
    v.name: v
    for v in (
        Viewport(name="mobile_320", width=320, height=568, deviceScaleFactor=2.0),
        Viewport(name="mobile_390", width=390, height=844, deviceScaleFactor=2.0),
        Viewport(name="tablet_834", width=834, height=1112, deviceScaleFactor=2.0),
        Viewport(name="desktop_1440", width=1440, height=900, deviceScaleFactor=1.0),
        Viewport(name="desktop_1920", width=1920, height=1080, deviceScaleFactor=1.0),
    )
}

DEFAULT_VIEWPORTS = [
    VIEWPORT_PRESETS["mobile_390"],
    VIEWPORT_PRESETS["tablet_834"],
    VIEWPORT_PRESETS["desktop_1440"],
]


class FigmaTolerances(ArtifactModel):
    """SPEC §7's table. Configurable per project; these are the defaults."""

    positionPx: float = 2.0
    sizePx: float = 2.0
    sizeRatio: float = 0.01
    colourDeltaE: float = 2.0
    fontSizePx: float = 0.5
    lineHeightPx: float = 1.0
    letterSpacingPx: float = 0.2
    spacingPx: float = 2.0
    radiusPx: float = 1.0
    borderWidthPx: float = 0.5
    opacity: float = 0.02


class MaskRegion(ArtifactModel):
    """A rectangle in CSS pixels, optionally only at one viewport."""

    x: float
    y: float
    w: float
    h: float
    viewport: str | None = None
    note: str = ""

    def box(self) -> Box:
        return Box(x=self.x, y=self.y, w=self.w, h=self.h)


class RunConfig(ArtifactModel):
    driver: str = "playwright"
    viewports: list[Viewport] = Field(default_factory=lambda: list(DEFAULT_VIEWPORTS))
    personas: list[str] = Field(default_factory=lambda: ["anonymous"])
    maxDepth: int = 3
    maxPages: int = 50
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    ignoreQueryParams: list[str] = Field(default_factory=list)
    sameOriginOnly: bool = True
    respectRobots: bool = True
    maskSelectors: list[str] = Field(default_factory=list)
    """Volatile things excluded from screenshots and from visual comparison: timestamps,
    carousels, randomised content, A/B variants (SPEC §5)."""

    consentSelectors: list[str] = Field(default_factory=list)
    vitalsSamples: int = 3
    """Loads per page used to measure web vitals. Each one costs a page load, so this is
    the dial between a run that is fast and a run whose performance findings hold still."""
    """Per-project overlay dismissers, tried before the heuristic pass (SPEC §5). Someone
    who has looked at the site knows better than any heuristic."""

    maskRegions: list[MaskRegion] = Field(default_factory=list)
    """For what a selector cannot reach — a canvas, a cross-origin iframe, an advert."""

    figmaFileKey: str | None = None
    figmaFrameMap: dict[str, str] = Field(default_factory=dict)
    """Frame id or name to page path or id. Confirmed once by a human and reused on every
    future run (SPEC §6)."""

    figmaPins: dict[str, str] = Field(default_factory=dict)
    """SPEC §7's escape hatch: `layerName → cssSelector`, always honoured."""

    figmaTolerances: FigmaTolerances = Field(default_factory=FigmaTolerances)
    templateSample: int = 5
    """How many pages to keep per templated shape (`/blog/*`) before sampling stops."""

    recordings: list[dict[str, Any]] = Field(default_factory=list)
    """Journeys someone recorded once (SPEC §15), as neutral steps rather than generated
    code. They travel with the run config so an artifact says exactly what was run."""

    colourScheme: Literal["light", "dark"] = "light"
    """What `prefers-color-scheme` reports to the site under test."""

    pageTimeoutMs: int = 30_000
    settleMs: int = 300
    maxElements: int = 5000
    maxLinkProbes: int = 500
    """Unique outbound links resolved per run. Beyond this they are left unchecked
    rather than silently reported as fine."""
    locale: str = "en-GB"
    timezone: str = "UTC"
    dictionary: list[str] = Field(default_factory=list)
    authorisedBy: str | None = None
    """Who signed off on testing this target. The engine refuses to exercise flows or
    probe an API without it — this is testing a system the user is contracted to test,
    and that has to be recorded, not assumed."""

    authorisedHosts: list[str] = Field(default_factory=list)
    """Hosts this run may send a request to beyond reading pages. Empty means the seed's
    own host and nothing else."""

    flows: bool = True
    apiProbes: bool = True
    flowRetries: int = 2
    """Per-project words the spell checker must accept: brand names, product names,
    jargon. Every false positive belongs here rather than in a lowered threshold."""


class RunManifest(ArtifactModel):
    """`run.json` — config, target, timings, status, git sha of the checker suite."""

    schemaVersion: int = SCHEMA_VERSION
    runId: str
    target: str
    status: RunStatus = RunStatus.pending
    startedAt: datetime
    finishedAt: datetime | None = None
    durationMs: int | None = None
    checkersSha: str | None = None
    projectId: str | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    pageIds: list[str] = Field(default_factory=list)


# -------------------------------------------------------------------------- page.json


class PageSecurity(ArtifactModel):
    """TLS facts for the main document response (SPEC §8.4 A)."""

    protocol: str | None = None
    issuer: str | None = None
    subjectName: str | None = None
    validFrom: float | None = None
    validTo: float | None = None


class PageRecord(ArtifactModel):
    """`pages/{page_id}/page.json`."""

    id: str
    url: str
    path: str
    title: str | None = None
    status: int
    redirectChain: list[str] = Field(default_factory=list)
    depth: int = 0
    discoveredFrom: str | None = None
    persona: str = "anonymous"
    domNodeCount: int = 0
    """Every element in the document, not just the ones captured (SPEC §8.4 G)."""

    security: PageSecurity | None = None
    crawlBlocked: str | None = None
    """Why bot protection stopped this page. Set means nothing was captured and no
    finding may ever be emitted from it (SPEC §5)."""


# ----------------------------------------------------------------------- console.json


class ConsoleMessage(ArtifactModel):
    level: str
    text: str
    stack: str | None = None
    url: str | None = None
    line: int | None = None
    ts: datetime


# ----------------------------------------------------------------------- network.json


class NetworkTiming(ArtifactModel):
    startMs: float
    ttfbMs: float | None = None
    durationMs: float


class NetworkSize(ArtifactModel):
    transferBytes: int
    resourceBytes: int | None = None


class NetworkEntry(ArtifactModel):
    url: str
    method: str
    status: int
    type: str
    reqHeaders: dict[str, str] = Field(default_factory=dict)
    resHeaders: dict[str, str] = Field(default_factory=dict)
    reqBody: str | None = None
    resBodyHash: str | None = None
    resBodySample: str | None = None
    timing: NetworkTiming
    size: NetworkSize
    initiator: str | None = None
    sourceMapUrl: str | None = None
    """The `//# sourceMappingURL=` comment, if the served script carries one. Recorded at
    capture time because it lives at the end of a body a checker never sees."""

    failure: str | None = None
    """Set when the request never produced a response at all (`net::ERR_…`). A 404 is a
    status, not a failure; this is for the ones with nothing on the other end."""


# ------------------------------------------------------------------------ vitals.json


class Metrics(ArtifactModel):
    """`vitals.json` — SPEC §4. INP is a proxy until a real interaction happens.

    Each value is the *median* of `sampleCount` loads, with the observed spread beside
    it. One load of a real site does not measure a page, it measures a moment: LCP moved
    850ms and CLS moved 0.09 between two runs of an unchanged site here, which is enough
    to walk findings across a threshold and break SPEC §20's byte-identical promise.
    """

    lcp: float | None = None
    cls: float | None = None
    tbt: float | None = None
    ttfb: float | None = None
    inp: float | None = None

    sampleCount: int = 1
    low: dict[str, float] = Field(default_factory=dict)
    high: dict[str, float] = Field(default_factory=dict)
    """Best and worst seen per metric. A finding needs the *whole* range past budget."""


# ---------------------------------------------------------------------- elements.json


class Box(ArtifactModel):
    x: float
    y: float
    w: float
    h: float


class ElementStyles(ArtifactModel):
    """Exactly the properties listed in SPEC §4.1 — never the full computed style."""

    color: str
    backgroundColor: str
    fontFamily: str
    fontSize: float
    fontWeight: int
    lineHeight: float | None = None  # computed `normal` has no px value
    letterSpacing: float = 0.0
    textTransform: str = "none"
    textAlign: str = "start"
    textOverflow: str = "clip"
    """Beyond SPEC §4.1's list, deliberately: "text clipped without ellipsis" (§8.4 B)
    cannot be decided without it, and the fix for a missing measurement is to capture it,
    never to fetch it at check time."""

    scrollMarginTop: float = 0.0
    """Likewise, for "sticky header covering anchor targets"."""

    opacity: float = 1.0
    marginTop: float = 0.0
    marginRight: float = 0.0
    marginBottom: float = 0.0
    marginLeft: float = 0.0
    paddingTop: float = 0.0
    paddingRight: float = 0.0
    paddingBottom: float = 0.0
    paddingLeft: float = 0.0
    borderRadius: Quad = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    borderWidth: Quad = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    borderColor: str = "rgb(0, 0, 0)"
    boxShadow: str = "none"
    display: str = "block"
    flexDirection: str | None = None
    gap: float = 0.0
    position: str = "static"
    zIndex: str = "auto"
    overflow: str = "visible"


class FontInfo(ArtifactModel):
    requested: str
    rendered: str
    fallbackUsed: bool


class ImageInfo(ArtifactModel):
    src: str
    naturalW: int
    naturalH: int
    renderedW: float
    renderedH: float
    bytes: int | None = None
    format: str | None = None
    loaded: bool = True
    alt: str | None = None
    loading: str | None = None


class FieldInfo(ArtifactModel):
    """A form control's contract with the user — SPEC §8.4 H.

    No value is ever captured. A field's *type* is what the battery needs; a field's
    contents are the user's, and on a password field they are a credential.
    """

    type: str = "text"
    name: str | None = None
    required: bool = False
    disabled: bool = False
    readOnly: bool = False
    placeholder: str | None = None
    pattern: str | None = None
    minLength: int | None = None
    maxLength: int | None = None
    min: str | None = None
    max: str | None = None
    step: str | None = None
    autocomplete: str | None = None
    accept: str | None = None
    multiple: bool = False
    options: list[str] = Field(default_factory=list)
    labelledBy: str | None = None
    formElementId: str | None = None


class FormInfo(ArtifactModel):
    action: str | None = None
    method: str = "get"
    name: str | None = None
    enctype: str | None = None
    noValidate: bool = False


class LinkInfo(ArtifactModel):
    href: str
    resolved: str
    target: str | None = None
    rel: str | None = None
    external: bool = False


class ElementRecord(ArtifactModel):
    """One entry per rendered element that is visible or has visible intent."""

    id: str
    stableKey: str
    selector: str
    tag: str
    role: str | None = None
    classes: list[str] = Field(default_factory=list)
    htmlId: str | None = None
    testId: str | None = None
    text: str = ""
    textLength: int = 0
    """Own text length before truncation. `text` is capped at 400 characters, so any
    arithmetic over it — line measure, for one — needs the real number."""

    textFull: str = ""
    box: Box
    boxViewport: Box
    scrollW: float = 0.0
    scrollH: float = 0.0
    """scrollWidth/scrollHeight. Bigger than the box means content is clipped — the only
    way a checker can see clipping without a browser."""

    visible: bool = True
    occludedBy: str | None = None
    clickable: bool = False
    focusable: bool = False
    tabIndex: int | None = None
    styles: ElementStyles
    resolvedBackground: str
    contrast: float | None = None
    font: FontInfo | None = None
    image: ImageInfo | None = None
    link: LinkInfo | None = None
    field: FieldInfo | None = None
    form: FormInfo | None = None
    parentId: str | None = None
    childIds: list[str] = Field(default_factory=list)
    domDepth: int = 0
    nearestHeading: str | None = None
    nearestLandmark: str | None = None


# ------------------------------------------------------------------------ layout.json


class AlignmentSet(ArtifactModel):
    """Siblings sharing an edge. Any member off the median by >1px is a candidate."""

    axis: Literal["x", "y"]
    edge: Literal["start", "end", "centre"] = "start"
    parentId: str | None = None
    median: float
    elementIds: list[str]
    deviations: dict[str, float] = Field(default_factory=dict)


class RepeatedGroup(ArtifactModel):
    """Siblings with a matching tag/class signature — card grids, nav items, listings."""

    signature: str
    parentId: str | None = None
    elementIds: list[str]

    @property
    def count(self) -> int:
        return len(self.elementIds)


class SpacingBucket(ArtifactModel):
    gap: float
    count: int


class TypeStyleUsage(ArtifactModel):
    fontFamily: str
    fontSize: float
    fontWeight: int
    lineHeight: float | None = None
    count: int


class ColourUsage(ArtifactModel):
    colour: str
    property: str
    count: int
    nearestToken: str | None = None
    deltaE: float | None = None


class LayoutRecord(ArtifactModel):
    """`layout.json` — derived once at capture because every layout checker needs it."""

    pageId: str
    viewport: str
    alignmentSets: list[AlignmentSet] = Field(default_factory=list)
    repeatedGroups: list[RepeatedGroup] = Field(default_factory=list)
    spacingHistogram: list[SpacingBucket] = Field(default_factory=list)
    typeInventory: list[TypeStyleUsage] = Field(default_factory=list)
    colourInventory: list[ColourUsage] = Field(default_factory=list)


# -------------------------------------------------------------------------- flows


class StepStatus(StrEnum):
    ok = "ok"
    failed = "failed"


class Step(ArtifactModel):
    """One logged action — SPEC §12.3. Reproduction steps are never written by hand."""

    n: int
    text: str
    ts: datetime
    url: str
    status: StepStatus = StepStatus.ok
    screenshot: str | None = None
    error: str | None = None
    durationMs: float = 0.0


class FlowStatus(StrEnum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"


class FlowFailure(ArtifactModel):
    """What went wrong, in the shape a checker turns into an Issue."""

    kind: str
    message: str
    step: int | None = None
    expected: str | None = None
    actual: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class FlowRecord(ArtifactModel):
    """`flows/{flow_id}/steps.json` — SPEC §4."""

    id: str
    name: str
    kind: str
    persona: str = "anonymous"
    pageId: str | None = None
    status: FlowStatus = FlowStatus.passed
    startedAt: datetime
    durationMs: int = 0
    attempts: int = 1
    """SPEC §5: a flow retries twice before it becomes an Issue. Half of flaky findings
    vanish on the second attempt."""

    steps: list[Step] = Field(default_factory=list)
    failures: list[FlowFailure] = Field(default_factory=list)
    trace: str | None = None
    video: str | None = None
    skippedBecause: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------- api probes


class Endpoint(ArtifactModel):
    """Derived free from the network capture: we already know every endpoint the site
    called (SPEC §8.4 I)."""

    id: str
    method: str
    template: str
    sampleUrl: str
    type: str = "xhr"
    status: int = 0
    seenOn: list[str] = Field(default_factory=list)
    requestContentType: str | None = None
    responseContentType: str | None = None
    hasAuthHeader: bool = False


class ProbeResult(ArtifactModel):
    endpointId: str
    probe: str
    method: str
    url: str
    status: int = 0
    finding: bool = False
    """True when this probe found something worth reporting. The checker decides how bad
    it is; the probe only decides whether it happened."""

    detail: str = ""
    durationMs: float = 0.0
    evidence: dict[str, Any] = Field(default_factory=dict)


class ApiReport(ArtifactModel):
    """`api/endpoints.json` and `api/probes.json`, together in memory."""

    authorisedBy: str | None = None
    authorisedHosts: list[str] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    probes: list[ProbeResult] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)


# ------------------------------------------------------------------------ probes.json


class LinkProbe(ArtifactModel):
    """One outbound link, resolved once per run rather than once per page."""

    url: str
    status: int
    internal: bool
    error: str | None = None
    foundOn: list[str] = Field(default_factory=list)


class PathProbe(ArtifactModel):
    """A single well-known path requested directly (SPEC §8.4 A, light probe)."""

    path: str
    status: int
    kind: str
    """`not-found-handling` or `exposed-path`."""

    bodySample: str | None = None
    bodyHash: str | None = None
    """SHA1 of the whole body, whitespace-collapsed.

    A soft-404 site answers 200 to everything, so a status alone cannot tell an exposed
    file from the app shell. The hash of what the 404 probe got back is what makes the
    difference checkable — by this checker and, through the resolution pass, by any other
    finding that rests on a 200 meaning "this exists".
    """


class ProbeReport(ArtifactModel):
    """`probes.json` — everything a checker needs that requires an HTTP request.

    Checkers never touch the network, so anything that has to be fetched is fetched here.
    """

    links: list[LinkProbe] = Field(default_factory=list)
    paths: list[PathProbe] = Field(default_factory=list)


# -------------------------------------------------------------------------- page bag


class PageArtifact(ArtifactModel):
    """Everything under `pages/{page_id}/` for one page, in memory.

    Used by the writer and the round-trip test. Checkers read through RunContext
    instead, which loads lazily.
    """

    page: PageRecord
    dom: str | None = None
    console: list[ConsoleMessage] = Field(default_factory=list)
    network: list[NetworkEntry] = Field(default_factory=list)
    vitals: Metrics | None = None
    axe: dict[str, Any] | None = None
    a11y: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    elements: dict[str, list[ElementRecord]] = Field(default_factory=dict)
    layout: dict[str, LayoutRecord] = Field(default_factory=dict)


class RunArtifact(ArtifactModel):
    """A whole run in memory. Written by `store.write_run`, read by `store.read_run`."""

    manifest: RunManifest
    pages: list[PageArtifact] = Field(default_factory=list)
    probes: ProbeReport | None = None
