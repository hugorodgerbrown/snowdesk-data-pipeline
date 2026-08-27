"""
tests/sentinels/fidelity.py — the CAAML fidelity coverage table and its machinery.

Snowdesk claims to render the provider's bulletin in full rather than a
simplified subset: a reader can follow the source link and find the same
data. That claim can go quietly false in two directions — a provider adds
a field nobody notices, or a template refactor drops one — and until
SNOW-671 nothing checked either. ``unscheduled`` (SNOW-670) was carried
through the fetcher, the translator and the render model for the whole
life of the project, and reached no template at all.

This module is the data half of the guard. It holds:

- ``flatten()``, which turns a CAAML ``properties`` dict into a set of
  dotted paths, one per leaf, with ``[]`` standing in for list membership
  so ``avalancheProblems[0].aspects[2]`` and
  ``avalancheProblems[1].aspects[0]`` collapse to one path;
- ``RENDERED``, the paths that reach a reader, each paired with a probe
  that says how to recognise the value on the page;
- ``EXCLUDED``, the paths that deliberately do not, each with a written
  reason.

``EXCLUDED`` is the valuable half. A field that no surface shows is a
decision, and the point of the table is that somebody had to write the
decision down. ``bin/fidelity-lint --show-exclusions`` prints it for cold
review, in the same spirit as ``bin/ds-lint --show-allows``.

The two consumers deliberately need different things, so this module
imports nothing from Django and nothing from the app tree:

- ``bin/fidelity-lint`` asks the structural question — is every path in
  every sentinel classified, does every exclusion carry a reason, and are
  there stale rows for paths no sentinel carries any more. That runs in
  the dependency-free lint matrix and is the half that catches a provider
  adding a field.
- ``tests/sentinels/test_fidelity.py`` asks the rendering question — does
  each ``RENDERED`` path's probe actually find its value on the page. That
  needs Django, a database and the full template stack, and is the half
  that catches a refactor dropping a field.

Adding a field to a sentinel payload fails ``bin/fidelity-lint`` until it
is classified. That failure is the feature.
"""

from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENTINELS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def _walk(node: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    """Yield ``(path, leaf_value)`` pairs for every scalar under *node*.

    Empty dicts and empty lists yield nothing: they carry no value, so
    there is nothing a page could be expected to show and nothing for a
    reviewer to make a decision about.

    Args:
        node: The current subtree — dict, list, or scalar.
        prefix: The dotted path accumulated so far ("" at the root).

    Yields:
        Tuples of dotted path and the scalar found at it.

    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, f"{prefix}[]")
    else:
        yield prefix, node


def flatten(properties: dict[str, Any]) -> dict[str, list[Any]]:
    """Flatten a CAAML ``properties`` dict into dotted path → values.

    List indices collapse to ``[]`` so that a path names a *field*, not an
    occurrence: the question the guard asks is whether
    ``avalancheProblems[].problemType`` reaches a reader, not whether the
    third problem's does.

    Args:
        properties: A CAAML v6 ``properties`` dict, as committed in a
            sentinel's ``source.json``.

    Returns:
        A mapping of dotted path to every leaf value found at that path,
        in document order.

    """
    found: dict[str, list[Any]] = {}
    for path, value in _walk(properties, ""):
        found.setdefault(path, []).append(value)
    return found


def sentinel_paths() -> dict[str, set[str]]:
    """Return every dotted path each committed sentinel carries.

    Returns:
        A mapping of sentinel id (e.g. ``"slf/A-single-level"``) to the
        set of dotted paths in its ``source.json``.

    """
    per_sentinel: dict[str, set[str]] = {}
    for path in sorted(SENTINELS_DIR.glob("*/*/source.json")):
        sentinel_id = str(path.relative_to(SENTINELS_DIR).parent)
        properties = json.loads(path.read_text(encoding="utf-8"))
        per_sentinel[sentinel_id] = set(flatten(properties))
    return per_sentinel


def all_sentinel_paths() -> set[str]:
    """Return the union of dotted paths across every committed sentinel."""
    return set().union(*sentinel_paths().values())


# ---------------------------------------------------------------------------
# The rendered page a probe is run against
# ---------------------------------------------------------------------------

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def visible_text(page_html: str) -> str:
    """Reduce a rendered page to the text a reader can actually see.

    Stripping ``<script>`` before anything else is load-bearing rather
    than tidy. In DEBUG the bulletin template embeds the entire raw CAAML
    payload in a ``<script type="application/json">`` block, and the
    JSON-LD block carries a second copy of much of the render model. A
    probe run against the raw response body would find every path in one
    of those and report perfect fidelity for a blank page.

    Args:
        page_html: The full response body.

    Returns:
        Casefolded, HTML-unescaped, whitespace-collapsed visible text.

    """
    stripped = _SCRIPT_OR_STYLE.sub(" ", page_html)
    stripped = _TAG.sub(" ", stripped)
    return _WHITESPACE.sub(" ", html_module.unescape(stripped)).strip().casefold()


@dataclass(frozen=True)
class RenderedPage:
    """One sentinel's bulletin page, in the shapes probes need.

    Attributes:
        sentinel_id: Which sentinel this is, e.g. ``"albina/A-single-level"``.
        text: Visible text, per :func:`visible_text`.
        html: The unmodified response body, for the few probes that need
            to see markup (an aspect rose is an SVG, not a word).
        render_model: The render model the page was built from.

    """

    sentinel_id: str
    text: str
    html: str
    render_model: dict[str, Any]


# A probe answers one question: given every value a sentinel carries at
# this path, can that value be found on this page? Returning False fails
# the build with the path named.
Probe = Callable[[list[Any], RenderedPage], bool]


# ---------------------------------------------------------------------------
# Probe factories
# ---------------------------------------------------------------------------


def _scalars(values: list[Any]) -> list[Any]:
    """Drop nulls, which carry nothing for a page to show."""
    return [v for v in values if v is not None]


def literal() -> Probe:
    """Probe: the value appears verbatim in the visible text."""

    def probe(values: list[Any], page: RenderedPage) -> bool:
        return all(str(v).casefold() in page.text for v in _scalars(values))

    return probe


def snake_label() -> Probe:
    """Probe: a snake_case enum appears as its humanised label.

    The app's own transform is ``value.replace("_", " ").capitalize()``
    (``apps/public/views.py``), with ``apps.bulletins.schema`` overriding
    a few with translated names. Matching on the spaced form catches both
    without this table having to duplicate the label map.
    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        return all(
            str(v).replace("_", " ").casefold() in page.text for v in _scalars(values)
        )

    return probe

    # (No fallback to the raw snake_case form on purpose: a page showing
    # "persistent_weak_layers" verbatim would be a bug, not coverage.)


def prose(min_words: int = 6) -> Probe:
    """Probe: a run of the prose body's own words appears in the text.

    Prose values are HTML, and the page does not render them whole: the
    leading ``<h1>``/``<h2>`` becomes the collapsible panel's title while
    the rest becomes its body, so the source string as committed never
    appears contiguously anywhere. This strips the markup, drops the
    leading heading, and looks for the first *min_words* words of what is
    left — enough to prove the body reached the page, short enough to
    survive a wrapping or punctuation change.

    Args:
        min_words: How many words of the body to match on.

    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            body = re.sub(r"^\s*<h[1-6]\b.*?</h[1-6]>", "", str(value), flags=re.S)
            plain = _WHITESPACE.sub(" ", html_module.unescape(_TAG.sub(" ", body)))
            words = plain.strip().casefold().split()
            if not words:
                continue
            if " ".join(words[:min_words]) not in page.text:
                return False
        return True

    return probe


def chip(testid: str) -> Probe:
    """Probe: a chip carrying *testid* renders with non-empty content.

    For enum fields whose visible label comes from a translated map the
    guard should not restate — ``avalancheType`` renders as "Slab" or,
    for everything else including ``glide``, "Loose", so asserting a
    label here would encode the template's own fallback as if it were the
    contract. Asserting the chip exists and says something proves the
    field still reaches a surface, which is the question this guard asks.

    Args:
        testid: The ``data-testid`` the surface carries.

    """
    pattern = re.compile(
        rf'data-testid="{re.escape(testid)}"[^>]*>\s*([^<]*\S[^<]*)<', re.IGNORECASE
    )

    def probe(values: list[Any], page: RenderedPage) -> bool:
        if not _scalars(values):
            return True
        return bool(pattern.search(page.html))

    return probe


def mapped(labels: dict[Any, str]) -> Probe:
    """Probe: each value's mapped label appears in the visible text.

    Used where the source value and what a reader sees share no
    characters — EAWS size 2 is "Medium", tendency ``steady`` is
    "Constant avalanche danger". The mapping is restated here rather than
    imported from ``apps.public.views`` on purpose: a guard that reads the
    app's own map moves in lockstep with it and would keep passing if the
    map were emptied.

    Args:
        labels: Source value → the text a reader should see.

    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            expected = labels.get(value)
            if expected is None:
                # A value the table has never seen. Failing is right: the
                # page's label for it is unknown, so nobody has checked
                # that it renders.
                return False
            if expected.casefold() not in page.text:
                return False
        return True

    return probe


def aspects() -> Probe:
    """Probe: a problem's aspect set reaches the aspect/elevation row.

    ``_rating_block.html`` renders one of two shapes per problem: the
    compass points comma-joined ("N, NE, E"), or the literal "All
    aspects" when that problem carries all eight. A bare "N" would match
    half the words on the page, so each point is looked for as a
    comma-delimited token.

    The all-aspects collapse is per problem, but ``flatten`` deliberately
    loses which problem a value came from — the table asks whether a
    *field* reaches a reader, not whether the third problem's copy does.
    So a page carrying any all-aspects problem satisfies every point,
    which is weaker than it could be. The alternative is a table keyed by
    occurrence, which would turn every row into a per-sentinel inventory
    and rot on the first payload change.
    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        if "all aspects" in page.text:
            return True
        return all(
            re.search(rf"(?:^|[\s,]){re.escape(point)}(?:[\s,]|$)", page.text)
            for point in {str(v).casefold() for v in _scalars(values)}
        )

    return probe


def danger_pattern() -> Probe:
    """Probe: an LWD danger pattern reaches its tag as the ``GM.N`` form.

    LWD_Tyrol publishes ``DP1``–``DP10``; the page normalises to the EAWS
    ``GM.1``–``GM.10`` display form, so the raw value never appears.
    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            raw = str(value).strip().lower()
            if not raw.startswith("dp"):
                return False
            if f"gm.{raw[2:]}" not in page.text:
                return False
        return True

    return probe


def subdivision_suffix() -> Probe:
    """Probe: an SLF subdivision reaches the Day Risk Profile row in words.

    SLF grades a rating within its level: ``plus`` sits at the top of the
    band, ``minus`` at the bottom, ``neutral`` in the middle. The first two
    render as "upper end of the band" / "lower end of the band" beside the
    level word; the third renders as nothing at all, which is why
    ``neutral`` passes without a needle.

    Before SNOW-727 this matched a suffix glyph on the problem card's
    level-number chip. That chip is gone — the card said the level three
    other ways — and the day-window tile, which renders the same glyph, is
    ``aria-hidden``. Matching the words instead means the probe now tracks a
    surface a screen reader can actually reach.
    """
    words = {"plus": "upper end of the band", "minus": "lower end of the band"}

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            phrase = words.get(str(value))
            if phrase is None:
                # "neutral" — and any future value the table has not seen,
                # which should fail rather than pass silently.
                if str(value) != "neutral":
                    return False
                continue
            if phrase not in page.text:
                return False
        return True

    return probe


def time_period_label() -> Probe:
    """Probe: a non-default time window reaches the Day Risk Profile row.

    ``all_day`` is the page's default window and is deliberately unlabelled
    (SNOW-727): naming it told the reader nothing on a single-window day,
    and on a split day it is merely the baseline the "later" row departs
    from. So ``all_day`` passes without a needle, the same way ``neutral``
    does in :func:`subdivision_suffix`.

    ``earlier`` and ``later`` are the news, and each renders as its own
    pill on the Day Risk Profile row. Note the pill is what this matches,
    not the problem card: SLF words the window in the card's title bar in
    its own register ("as the day progresses"), which no enum-derived label
    would ever match.
    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            token = str(value)
            if token == "all_day":
                continue
            if token not in {"earlier", "later"}:
                # A window the table has not seen — fail rather than pass
                # silently, so a new enum member surfaces here.
                return False
            if token not in page.text:
                return False
        return True

    return probe


def timestamp() -> Probe:
    """Probe: an ISO timestamp reaches the metadata strip as ``j M H:i``.

    The strip renders every timestamp through Django's ``date`` filter
    with ``"j M H:i"``, in UTC (``TIME_ZONE`` is UTC and the strip appends
    a literal "UTC"). So ``2025-11-28T18:06:01Z`` reaches a reader as
    "28 Nov 18:06" and no substring of the source string appears anywhere.
    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            when = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
                UTC
            )
            needle = f"{when.day} {when:%b} {when:%H:%M}".casefold()
            if needle not in page.text:
                return False
        return True

    return probe


def day() -> Probe:
    """Probe: an ISO timestamp reaches the page as a ``j F Y`` day.

    The outlook block names the day its forecast covers rather than a
    time, so it formats through ``"j F Y"`` — "30 November 2025".
    """

    def probe(values: list[Any], page: RenderedPage) -> bool:
        for value in _scalars(values):
            when = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
                UTC
            )
            if f"{when.day} {when:%B} {when.year}".casefold() not in page.text:
                return False
        return True

    return probe


def boolean_marker(testid: str) -> Probe:
    """Probe: a marker is on the page if and only if the flag is true.

    Both directions matter for a flag whose whole point is that it is
    usually absent. A probe that only checked the true case would pass
    against a page that rendered the marker unconditionally, which is
    the other way this field can be wrong.

    Args:
        testid: The ``data-testid`` of the element the flag controls.

    """
    needle = f'data-testid="{testid}"'

    def probe(values: list[Any], page: RenderedPage) -> bool:
        return any(bool(v) for v in _scalars(values)) == (needle in page.html)

    return probe


# ---------------------------------------------------------------------------
# The coverage table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rendered:
    """A CAAML path that reaches a reader, and how to recognise it.

    Attributes:
        probe: How to find this path's value on the rendered page.
        surface: Where on the page a reader finds it. Prose, for the
            reviewer reading this table cold and for the failure message.

    """

    probe: Probe
    surface: str


@dataclass(frozen=True)
class Excluded:
    """A CAAML path that deliberately reaches no reader, and why.

    Attributes:
        reason: Why no surface shows this. Required and non-empty — the
            whole point of the table is that every omission is a decision
            somebody wrote down, and a blank reason fails the lint.
        duplicate_of: The path whose rendering already carries this
            value, when the reason is duplication. Set it and the
            "excluded paths stay off the page" check skips this row,
            because the value legitimately does appear — under another
            name.

    """

    reason: str
    duplicate_of: str | None = None


#: EAWS avalanche sizes, per ``_AVALANCHE_SIZE_LABELS``.
_SIZE_LABELS = {
    1: "small",
    2: "medium",
    3: "large",
    4: "very large",
    5: "extremely large",
}

#: Tendency directions, per ``_TENDENCY_LABELS``.
_TENDENCY_LABELS = {
    "steady": "constant avalanche danger",
    "increasing": "increasing avalanche danger",
    "decreasing": "decreasing avalanche danger",
}


#: Paths that reach a reader. Every entry names the surface it lands on,
#: so a failure says what broke rather than only what is missing.
#:
#: A key may be scoped to one provider as ``"<provider>:<path>"``, which
#: wins over the bare path for that provider's sentinels. That is not a
#: convenience: the same CAAML field can be a different kind of thing
#: depending on who sent it, and pretending otherwise would force one of
#: the two into a lie.
RENDERED: dict[str, Rendered] = {
    "avalancheActivity.comment": Rendered(prose(), "Avalanche activity section body"),
    "avalancheActivity.highlights": Rendered(
        literal(), "Avalanche activity lead paragraph"
    ),
    "avalancheProblems[].aspects[]": Rendered(
        aspects(), "Problem card aspect/elevation row"
    ),
    "avalancheProblems[].avalancheSize": Rendered(
        mapped(_SIZE_LABELS), "EAWS size chip on the problem card"
    ),
    "avalancheProblems[].comment": Rendered(prose(), "Problem card body prose"),
    "avalancheProblems[].customData.ALBINA.avalancheType": Rendered(
        chip("avalanche-type-chip"), "Avalanche-type chip on the problem card"
    ),
    "avalancheProblems[].customData.CH.subdivision": Rendered(
        subdivision_suffix(),
        "Day Risk Profile row, in words (SNOW-727), and the problem card's "
        "level tile as a suffix (SNOW-739). Neither reads this field: both take the "
        "period's rating at their own level, resolved by "
        "_subdivision_for_period.",
    ),
    "avalancheProblems[].dangerRatingValue": Rendered(
        literal(), "Problem card danger level"
    ),
    "avalancheProblems[].elevation.lowerBound": Rendered(
        literal(), "Problem card aspect/elevation row (above N m)"
    ),
    "avalancheProblems[].elevation.upperBound": Rendered(
        literal(), "Problem card aspect/elevation row (below N m)"
    ),
    "avalancheProblems[].frequency": Rendered(
        snake_label(), "EAWS frequency chip on the problem card"
    ),
    "avalancheProblems[].problemType": Rendered(snake_label(), "Problem card heading"),
    "avalancheProblems[].snowpackStability": Rendered(
        snake_label(), "EAWS stability chip on the problem card"
    ),
    "avalancheProblems[].validTimePeriod": Rendered(
        time_period_label(),
        "Day Risk Profile row, and the card's title bar after a middot. The "
        "card's own time pill went in SNOW-727; the SNOW-739 suffix is "
        "suppressed "
        "when the provider's title already names the window.",
    ),
    "customData.CH.aggregation[].category": Rendered(
        literal(),
        "Problem card title bar — the provider's own wording names the "
        "category ('Dry avalanches', 'Wet-snow avalanches'). The separate "
        "dry/wet pill was removed in SNOW-727 as a fourth telling of it.",
    ),
    "customData.CH.aggregation[].problemTypes[]": Rendered(
        snake_label(), "Problem cards grouped under the trait block"
    ),
    "customData.CH.aggregation[].title": Rendered(literal(), "Trait block heading"),
    "customData.CH.aggregation[].validTimePeriod": Rendered(
        time_period_label(), "Day Risk Profile row"
    ),
    "customData.LWD_Tyrol.dangerPatterns[]": Rendered(
        danger_pattern(), "Danger-pattern tags below the problem cards"
    ),
    "customData.MF.j2Outlook.comment": Rendered(prose(), "Outlook collapsible panel"),
    "dangerRatings[].customData.CH.subdivision": Rendered(
        subdivision_suffix(),
        "Day Risk Profile row, beside the level word: 'upper end of the "
        "band' / 'lower end of the band'",
    ),
    "dangerRatings[].elevation.lowerBound": Rendered(
        literal(), "Elevation-band heading above the trait block"
    ),
    "dangerRatings[].elevation.upperBound": Rendered(
        literal(), "Elevation-band heading above the trait block"
    ),
    "dangerRatings[].mainValue": Rendered(
        literal(), "Day Risk Profile row and hero rating"
    ),
    "dangerRatings[].validTimePeriod": Rendered(
        time_period_label(), "Day Risk Profile row"
    ),
    "nextUpdate": Rendered(timestamp(), "Metadata strip, Next update cell"),
    "publicationTime": Rendered(timestamp(), "Metadata strip, Issued cell"),
    "snowpackStructure.comment": Rendered(prose(), "Snowpack collapsible panel"),
    "tendency[].comment": Rendered(prose(), "Outlook collapsible panel"),
    "tendency[].highlights": Rendered(literal(), "Outlook block prose"),
    "tendency[].tendencyType": Rendered(
        mapped(_TENDENCY_LABELS), "Outlook block label and arrow"
    ),
    "tendency[].validTime.endTime": Rendered(day(), "Outlook block target date"),
    "unscheduled": Rendered(
        boolean_marker("unscheduled-marker"),
        "Off-schedule marker above the day character, and the Schedule cell",
    ),
    "validTime.endTime": Rendered(timestamp(), "Metadata strip, Valid until cell"),
    "weatherForecast.comment": Rendered(prose(), "Weather forecast collapsible panel"),
    "weatherReview.comment": Rendered(prose(), "Weather review collapsible panel"),
}


#: Paths that deliberately reach no reader, each with the reason. This is
#: the half worth reviewing: every line is a decision somebody made about
#: what a reader does not need. Print it with
#: ``bin/fidelity-lint --show-exclusions``.
#:
#: Provider scoping works the same way as in RENDERED.
EXCLUDED: dict[str, Excluded] = {
    "avalancheProblems[].customData.CH.coreZoneText": Excluded(
        "SLF's core-zone sentence restates, in prose, the subdivision and "
        "the problem's own aspect and elevation geography. All three are "
        "rendered structurally — the Day Risk Profile row's subdivision "
        "words and the card's aspect/elevation row — so the sentence would "
        "say the same thing a second time, in the provider's phrasing "
        "rather than the page's.",
        duplicate_of="avalancheProblems[].customData.CH.subdivision",
    ),
    "bulletinID": Excluded(
        "The provider's identifier for the bulletin. It is how the row is "
        "stored, deduplicated and served over /api/, and it names nothing "
        "a reader is looking for. Staff can see it on the page's debug "
        "block; nobody else needs to."
    ),
    "customData.ALBINA.mainDate": Excluded(
        "ALBINA's restatement of the day the bulletin covers. That day is "
        "the most prominent thing on the page, but derived from the "
        "validity window and the URL (target_day_for_valid_from) rather "
        "than read from here — the page has one answer for which day it "
        "is showing and does not take a second one from the payload."
    ),
    "customData.MF.amendment": Excluded(
        "Meteo-France's own name for an off-schedule reissue. The "
        "translator projects it onto the standard `unscheduled` flag, "
        "which SNOW-670 renders; carrying the provider-specific spelling "
        "to a second surface would just be the same fact twice.",
        duplicate_of="unscheduled",
    ),
    "customData.MF.images.aspectRose": Excluded(
        "A filename for a PNG Meteo-France publishes on its own site. The "
        "page draws its own aspect rose from the structured aspects, "
        "which stays legible in dark mode and at any size."
    ),
    "customData.MF.images.danger": Excluded(
        "A filename for a PNG Meteo-France publishes on its own site. The "
        "page draws the danger scale itself from `dangerRatings`."
    ),
    "customData.MF.j2Outlook.date": Excluded(
        "The day the J+2 outlook covers. The translator builds the "
        "tendency validity window from it, and the outlook block renders "
        "that window's end as its target date.",
        duplicate_of="tendency[].validTime.endTime",
    ),
    "customData.MF.j2Outlook.maxDanger": Excluded(
        "The J+2 danger level. The translator compares it against today's "
        "to derive `tendencyType` (_evolution_from_levels), so it reaches "
        "the reader as the outlook's direction and arrow rather than as a "
        "number.",
        duplicate_of="tendency[].tendencyType",
    ),
    "customData.MF.j2Outlook.label": Excluded(
        "Meteo-France's French name for the J+2 danger level ('Indice de "
        "risque faible'). The translator copies it to `tendency[].highlights`, "
        "which is excluded for Meteo-France for the reason recorded there — "
        "it is a level restated in words, not forecaster prose, and the "
        "outlook already carries the level's direction."
    ),
    "customData.MF.massif": Excluded(
        "The massif name, which the translator also writes to "
        "`regions[].name`. It reaches the reader as the region heading — "
        "read from the regions table, not from the payload.",
        duplicate_of="regions[].name",
    ),
    "customData.MF.mfInternalId": Excluded(
        "Meteo-France's internal numeric massif id, the key its DPBRA feed "
        "is addressed by. An implementation detail of fetching, not a fact "
        "about the snowpack."
    ),
    "customData.MF.rawLocalTimes.issuedAt": Excluded(
        "Preserved so the local-to-UTC conversion can be debugged against "
        "the source XML (docs/meteofrance-mapping.md). The converted UTC "
        "timestamp is what the metadata strip renders.",
        duplicate_of="publicationTime",
    ),
    "customData.MF.rawLocalTimes.publishedAt": Excluded(
        "Preserved so the local-to-UTC conversion can be debugged against "
        "the source XML (docs/meteofrance-mapping.md). The converted UTC "
        "timestamp is what the metadata strip renders.",
        duplicate_of="publicationTime",
    ),
    "customData.MF.rawLocalTimes.validTo": Excluded(
        "Preserved so the local-to-UTC conversion can be debugged against "
        "the source XML (docs/meteofrance-mapping.md). The converted UTC "
        "timestamp is what the metadata strip renders.",
        duplicate_of="validTime.endTime",
    ),
    "customData.MF.redundantProse.accidentel": Excluded(
        "The DPBRA `Accidentel` element, kept verbatim for completeness. "
        "The translator already folds it into `avalancheActivity`, which "
        "the page renders.",
        duplicate_of="avalancheActivity.comment",
    ),
    "customData.MF.redundantProse.naturel": Excluded(
        "The DPBRA `Naturel` element, kept verbatim for completeness. The "
        "translator already folds it into `avalancheActivity`, which the "
        "page renders.",
        duplicate_of="avalancheActivity.highlights",
    ),
    "customData.MF.snowCover.date": Excluded(
        "The date the snow-depth survey was taken. It qualifies figures "
        "the page does not show — see the depthsCm rows below."
    ),
    "customData.MF.snowCover.depthsCm[].altitudeM": Excluded(
        "Meteo-France publishes snow depth by altitude and aspect as "
        "structured data. The page has no snow-depth surface to put it "
        "on; the provider's own enneigement prose covers the same ground "
        "and is rendered in the Snowpack panel. Building the table is "
        "SNOW-695's scope, not a decision that a reader should never see "
        "it."
    ),
    "customData.MF.snowCover.depthsCm[].north": Excluded(
        "Snow depth on north-facing slopes at one altitude. No snow-depth "
        "surface exists yet — see snowCover.depthsCm[].altitudeM."
    ),
    "customData.MF.snowCover.depthsCm[].south": Excluded(
        "Snow depth on south-facing slopes at one altitude. No snow-depth "
        "surface exists yet — see snowCover.depthsCm[].altitudeM."
    ),
    "customData.MF.snowCover.snowLineNorthM": Excluded(
        "The snow line on northerly aspects. No snow-depth surface exists "
        "yet — see snowCover.depthsCm[].altitudeM."
    ),
    "customData.MF.snowCover.snowLineSouthM": Excluded(
        "The snow line on southerly aspects. No snow-depth surface exists "
        "yet — see snowCover.depthsCm[].altitudeM."
    ),
    "lang": Excluded(
        "The language the provider wrote this bulletin in. It selects the "
        "prose parser (apps.bulletins.services.prose) and nothing else. "
        "The page's own language is the reader's, not the payload's."
    ),
    "meteofrance:tendency[].highlights": Excluded(
        "For Meteo-France this field is `j2Outlook.label` — the French "
        "name of the J+2 danger level ('Indice de risque faible'), not "
        "forecaster prose. The render model projects `tendency_lead` from "
        "ALBINA only, deliberately (render_model.py, version 5), because "
        "the two providers put different kinds of thing in the same slot: "
        "one writes a sentence, the other repeats a level the outlook "
        "already carries as its direction."
    ),
    "regions[].name": Excluded(
        "The provider's name for each region the bulletin covers. Region "
        "identity is the regions table's job, not the payload's — the "
        "heading, the URL slug and every cross-link read from there, so "
        "one region is named one way across the whole site whichever "
        "provider is describing it that day."
    ),
    "regions[].regionID": Excluded(
        "The list of regions this bulletin is served under. It is a "
        "routing key: each region gets its own page, and a reader on one "
        "of them has no use for the other ninety-nine."
    ),
    "tendency[].validTime.startTime": Excluded(
        "When the outlook window opens, which is the end of the bulletin's "
        "own validity — the outlook covers the day after this one. The "
        "block names the day it forecasts (the window's end) because that "
        "is the day a reader is planning for; opening with 'from tomorrow "
        "16:00' would restate the validity strip above it."
    ),
    "validTime.startTime": Excluded(
        "When the assessment starts applying. The page derives its own "
        "date from it (target_day_for_valid_from), so a reader meets it "
        "as which day they are looking at. The metadata strip shows the "
        "window's end instead, because that is the end a reader acts on — "
        "when this assessment stops being true."
    ),
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

#: Sentinel subdirectories, which are also the provider scope names.
PROVIDERS: tuple[str, ...] = ("albina", "meteofrance", "slf")


def provider_of(sentinel_id: str) -> str:
    """Return the provider a sentinel id belongs to.

    Args:
        sentinel_id: e.g. ``"albina/A-single-level"``.

    Returns:
        The provider scope name, e.g. ``"albina"``.

    """
    return sentinel_id.split("/")[0]


def resolve(path: str, provider: str) -> Rendered | Excluded | None:
    """Return the table row governing *path* for *provider*.

    A provider-scoped row (``"meteofrance:tendency[].highlights"``) wins
    over the bare path, so one provider can differ without the other
    having to pretend.

    Args:
        path: A dotted CAAML path.
        provider: One of :data:`PROVIDERS`.

    Returns:
        The governing row, or ``None`` when the path is unclassified —
        which is the failure ``bin/fidelity-lint`` exists to report.

    """
    scoped = f"{provider}:{path}"
    for table in (RENDERED, EXCLUDED):
        if scoped in table:
            return table[scoped]
    for table in (RENDERED, EXCLUDED):
        if path in table:
            return table[path]
    return None


def table_keys() -> set[str]:
    """Return every key in both halves of the table, scoped keys included."""
    return set(RENDERED) | set(EXCLUDED)


def split_key(key: str) -> tuple[str | None, str]:
    """Split a table key into its optional provider scope and its path.

    Args:
        key: A table key, scoped (``"slf:lang"``) or bare (``"lang"``).

    Returns:
        ``(provider_or_None, path)``.

    """
    provider, _, rest = key.partition(":")
    if provider in PROVIDERS and rest:
        return provider, rest
    return None, key
