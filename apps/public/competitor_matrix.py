"""
apps/public/competitor_matrix.py — the feature matrix rendered on /compare/ (SNOW-836).

The matrix is data rather than table markup for two reasons. A hand-written
grid of thirteen features across seven products is 91 cells, and a template
carrying 91 hand-typed cells drifts row by row — a column silently gains a
tick because the cell beside it did. And the honesty properties the page
rests on are checkable here and nowhere else: that no competitor is marked
absent on evidence we do not have, and that our own column is not a clean
sweep.

**Four states, and the fourth is the point.** ``UNKNOWN`` is not a gap in
the data to be filled in later with a dash. ``docs/competitors.md`` records
what was CHECKED, and most of these products were checked for coverage,
price and headline features rather than interrogated feature by feature. A
product that is not described as doing something may simply never have been
asked. Rendering that as "no" would be the single most misleading thing
this page could do — it would manufacture an advantage for us out of an
absence in our own notes. So it renders as "not established", and the
legend says so in the reader's own words.

Sources, and which one governs:

* Every competitor cell traces to ``docs/competitors.md``, the weekly
  competitor scan. Nothing here may be inferred from a product's category
  or from what a similar product does.
* **Every Snowdesk cell is verified against this repository**, never
  against ``docs/competitors.md``. That doc twice recorded us as lacking
  something we ship (the slope overlay behind ``settings.SLOPE_TILE_URL``,
  and the national basemaps in ``settings.BASEMAP_STYLES``), and it is the
  page's own input, so the stale claim has a short route back in. Each
  Snowdesk cell below carries the symbol it was checked against.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    # ``django_stubs_ext`` is a typing-only dependency; ``from __future__
    # import annotations`` keeps every reference below a forward string at
    # runtime, so the import costs nothing outside the mypy env. Same idiom
    # as apps/bulletins/services/day_summary.py, and for the same reason:
    # every string in this module is a lazy translation, not a ``str``.
    from django_stubs_ext import StrOrPromise


class Support(enum.StrEnum):
    """How completely one product offers one feature.

    ``UNKNOWN`` is a first-class answer, not a placeholder — see the module
    docstring. It means "our sources do not say", which is different from
    ``NO`` and must never be rendered as one.
    """

    YES = "yes"
    PARTIAL = "partial"
    NO = "no"
    UNKNOWN = "unknown"


#: Screen-reader text and legend copy per state. The glyph is decorative —
#: it is hidden from assistive technology and the label is read instead, so
#: a screen-reader user gets "not established" rather than a question mark.
SUPPORT_LABELS: dict[Support, "StrOrPromise"] = {
    Support.YES: _("Yes"),
    Support.PARTIAL: _("Partly"),
    Support.NO: _("No"),
    Support.UNKNOWN: _("Not established"),
}

SUPPORT_GLYPHS: dict[Support, str] = {
    Support.YES: "●",
    Support.PARTIAL: "◐",
    Support.NO: "○",
    Support.UNKNOWN: "·",
}


@dataclass(frozen=True)
class Product:
    """One column of the matrix."""

    key: str
    name: "StrOrPromise"
    #: Short qualifier shown under the name — who makes it, or the scope
    #: caveat that would otherwise have to be repeated in every cell.
    note: "StrOrPromise" = ""


@dataclass(frozen=True)
class Cell:
    """One product's answer for one feature."""

    support: Support
    #: Optional four-or-five-word qualifier. Only where the state alone
    #: would mislead — ``PARTIAL`` almost always needs one, ``YES`` rarely.
    note: "StrOrPromise" = ""

    @property
    def glyph(self) -> str:
        """The decorative mark. Hidden from assistive technology."""
        return SUPPORT_GLYPHS[self.support]

    @property
    def label(self) -> "StrOrPromise":
        """The text a screen reader announces in the glyph's place."""
        return SUPPORT_LABELS[self.support]


@dataclass(frozen=True)
class Feature:
    """One row of the matrix."""

    key: str
    label: "StrOrPromise"
    #: One sentence on what the row is actually asking, shown beneath the
    #: label. Several of these rows sound self-explanatory and are not:
    #: "works offline" means different things to a native app and a website.
    question: "StrOrPromise"
    cells: dict[str, Cell] = field(default_factory=dict)


PRODUCTS: tuple[Product, ...] = (
    Product("whiterisk", _("WhiteRisk"), _("SLF")),
    Product("snowsafe", _("SnowSafe"), _("First Line")),
    Product("clarity", _("AvalancheClarity"), _("Simon Perry")),
    Product("skitourenguru", _("Skitourenguru / Yéti"), _("Schmudlach / Petzl")),
    Product("whympr", _("Whympr"), _("Chamonix")),
    Product("opensnow", _("OpenSnow"), _("Cloudnine")),
    Product("snowdesk", _("Snowdesk"), _("this site")),
)


def _cells(**kwargs: Cell) -> dict[str, Cell]:
    """Build a row's cell map, checking every product is answered.

    A feature added without an answer for one product would render as a
    blank cell that reads as "no" — the exact failure this module exists to
    prevent. Raising here makes it a startup error instead.

    Args:
        kwargs: One ``Cell`` per product key in :data:`PRODUCTS`.

    Returns:
        The cell map, keyed by product key.

    Raises:
        ValueError: If any product in :data:`PRODUCTS` is unanswered.

    """
    missing = {product.key for product in PRODUCTS} - set(kwargs)
    if missing:
        raise ValueError(f"feature row is missing answers for: {sorted(missing)}")
    return dict(kwargs)


_YES = Cell(Support.YES)
_NO = Cell(Support.NO)
_UNKNOWN = Cell(Support.UNKNOWN)


FEATURES: tuple[Feature, ...] = (
    Feature(
        key="rendered",
        label=_("Reads the bulletin to you"),
        question=_(
            "Does it render the forecast itself, or hand you off to the official page?"
        ),
        cells=_cells(
            whiterisk=_YES,
            snowsafe=_YES,
            clarity=Cell(Support.PARTIAL, _("Switzerland and France only")),
            skitourenguru=Cell(Support.PARTIAL, _("Scores a tour, not a render")),
            whympr=Cell(Support.PARTIAL, _("Pass-through of the official page")),
            opensnow=Cell(Support.PARTIAL, _("US and Canada forecasts")),
            snowdesk=_YES,
        ),
    ),
    Feature(
        key="multi_country",
        label=_("More than one country"),
        question=_("Can one app cover a trip that crosses a border?"),
        cells=_cells(
            whiterisk=Cell(Support.PARTIAL, _("Switzerland and France")),
            snowsafe=_YES,
            clarity=_YES,
            skitourenguru=Cell(Support.PARTIAL, _("Alps-wide, or France only")),
            whympr=_YES,
            opensnow=Cell(Support.NO, _("US and Canada only")),
            snowdesk=_YES,
        ),
    ),
    Feature(
        key="free_bulletin",
        label=_("Free to read the forecast"),
        question=_("Is the bulletin itself behind a subscription?"),
        cells=_cells(
            # Store listings, 2026-09-05: free to install with in-app
            # purchases. The bulletin sits in the free app — Play's own
            # description lists it as included, and the paid tiers are the
            # topo maps and the e-learning.
            whiterisk=Cell(Support.YES, _("Paid tiers are maps and lessons")),
            snowsafe=Cell(Support.YES, _("But the free app carries ads")),
            # The only competitor here with NO in-app purchases at all.
            clarity=Cell(Support.YES, _("No in-app purchases")),
            skitourenguru=_YES,
            whympr=_YES,
            opensnow=_YES,
            snowdesk=_YES,
        ),
    ),
    # The two absence-shaped rows. Both are phrased so that ● is still the
    # answer a reader wants, which keeps one polarity across the whole
    # grid — a row where a filled dot meant "this app has ads" would be
    # read wrongly by everyone skimming, however carefully it was
    # labelled. Values come from the store listings, 2026-09-05.
    #
    # These exist because a feature matrix counts what a product HAS, so
    # it rewards accumulation and gives nothing back for restraint. Two
    # of the things a person actually meets when they open one of these
    # apps — an advert, and a prompt to pay — are invisible in every
    # other row here.
    Feature(
        key="no_ads",
        label=_("Free of advertising"),
        question=_("Whether the app shows you adverts while you read."),
        cells=_cells(
            whiterisk=_YES,
            snowsafe=Cell(Support.NO, _("Play lists it as containing ads")),
            clarity=Cell(Support.YES, _("States no tracking and no ads")),
            skitourenguru=_UNKNOWN,
            whympr=_YES,
            opensnow=_YES,
            snowdesk=_YES,
        ),
    ),
    Feature(
        key="no_iap",
        label=_("Costs nothing after you start"),
        question=_(
            "Every app here is free to open. This is whether it stays that way."
        ),
        cells=_cells(
            whiterisk=Cell(Support.NO, _("Maps and lessons are paid")),
            snowsafe=Cell(Support.NO, _("Paid weather tier")),
            clarity=Cell(Support.YES, _("No in-app purchases")),
            skitourenguru=Cell(Support.YES, _("Free since foundation funding")),
            whympr=Cell(Support.NO, _("Premium, plus paid content")),
            opensnow=Cell(Support.NO, _("Two paid tiers")),
            snowdesk=Cell(Support.YES, _("Nothing to buy — see the note below")),
        ),
    ),
    Feature(
        key="offline",
        label=_("Works without signal"),
        question=_("Can you read the forecast in a valley with no reception?"),
        cells=_cells(
            whiterisk=_YES,
            snowsafe=_YES,
            clarity=_UNKNOWN,
            skitourenguru=_UNKNOWN,
            whympr=_YES,
            opensnow=Cell(Support.PARTIAL, _("Resort trail maps")),
            snowdesk=_YES,
        ),
    ),
    Feature(
        key="web",
        label=_("Works in a browser"),
        question=_("Or must you install an app to see anything at all?"),
        cells=_cells(
            whiterisk=_YES,
            snowsafe=Cell(Support.NO, _("No web surface")),
            clarity=Cell(Support.YES, _("Plus an embeddable widget")),
            skitourenguru=_YES,
            whympr=_YES,
            opensnow=_YES,
            snowdesk=Cell(Support.YES, _("Browser only — nothing to install")),
        ),
    ),
    Feature(
        key="alerts",
        label=_("Tells you when it changes"),
        question=_("A notification on rising danger, or on a bulletin re-issue."),
        cells=_cells(
            whiterisk=_UNKNOWN,
            snowsafe=Cell(Support.YES, _("Danger levels and daily updates")),
            clarity=Cell(Support.YES, _("New and revised bulletins")),
            skitourenguru=_UNKNOWN,
            whympr=_UNKNOWN,
            opensnow=Cell(Support.YES, _("Snowfall thresholds")),
            snowdesk=_NO,
        ),
    ),
    Feature(
        key="slope",
        label=_("Slope-angle layer"),
        question=_("Shading that shows you how steep the ground is."),
        cells=_cells(
            whiterisk=_YES,
            snowsafe=_UNKNOWN,
            clarity=Cell(Support.NO, _("No terrain tooling")),
            skitourenguru=_UNKNOWN,
            whympr=_YES,
            opensnow=_UNKNOWN,
            # settings.SLOPE_TILE_URL — swisstopo "Slope classes over 30°",
            # open to every visitor since SNOW-724 retired the flag.
            snowdesk=Cell(Support.PARTIAL, _("Alps coverage is not complete")),
        ),
    ),
    Feature(
        key="topo",
        label=_("National topographic maps"),
        question=_("The survey maps people actually navigate from."),
        cells=_cells(
            whiterisk=_YES,
            snowsafe=_UNKNOWN,
            clarity=_UNKNOWN,
            skitourenguru=_UNKNOWN,
            whympr=Cell(Support.YES, _("Eleven of them")),
            opensnow=_UNKNOWN,
            # settings.BASEMAP_STYLES — swisstopo winter and light, IGN Plan,
            # basemap.at, alongside OpenFreeMap.
            snowdesk=Cell(Support.YES, _("Switzerland, France, Austria")),
        ),
    ),
    Feature(
        key="stations",
        label=_("Live weather stations"),
        question=_("Observed snow and wind, not a forecast of them."),
        cells=_cells(
            whiterisk=Cell(Support.YES, _("Free")),
            snowsafe=_YES,
            clarity=_UNKNOWN,
            skitourenguru=_UNKNOWN,
            whympr=_UNKNOWN,
            opensnow=_UNKNOWN,
            # apps/weather/ is Open-Meteo forecast only — one row per
            # location per day, refreshed 4×/day. No observation network.
            snowdesk=Cell(Support.NO, _("Forecasts only")),
        ),
    ),
    Feature(
        key="tour_score",
        label=_("Rates a specific tour"),
        question=_("Today's bulletin combined with the terrain a route crosses."),
        cells=_cells(
            whiterisk=Cell(Support.YES, _("Flags cruxes on a drawn route")),
            snowsafe=Cell(Support.NO, _("Not a planner")),
            clarity=_NO,
            skitourenguru=Cell(Support.YES, _("The whole product")),
            whympr=_NO,
            opensnow=_NO,
            # apps/routes/ holds geometry only; no bulletin coupling.
            snowdesk=_NO,
        ),
    ),
    Feature(
        key="own_route",
        label=_("Your own route"),
        question=_("Bringing a track you already have, rather than picking theirs."),
        cells=_cells(
            whiterisk=Cell(Support.PARTIAL, _("Draw it in the app")),
            snowsafe=Cell(Support.NO, _("No route handling")),
            clarity=_NO,
            skitourenguru=Cell(Support.NO, _("A curated database")),
            whympr=_YES,
            opensnow=_UNKNOWN,
            # apps/routes/ — GPX upload, parsed and discarded.
            snowdesk=Cell(Support.YES, _("Upload a GPX file")),
        ),
    ),
    Feature(
        key="shared_plan",
        label=_("Share the plan with others"),
        question=_("One trip several people can see before the day, not after it."),
        cells=_cells(
            whiterisk=_UNKNOWN,
            snowsafe=_UNKNOWN,
            clarity=_UNKNOWN,
            skitourenguru=_UNKNOWN,
            whympr=Cell(Support.PARTIAL, _("Reports after the outing")),
            opensnow=_UNKNOWN,
            # apps/trips/ — Trip + TripParticipant, one share link, roster.
            snowdesk=Cell(Support.YES, _("One link adds you to the roster")),
        ),
    ),
    Feature(
        key="field_reports",
        label=_("Report what you saw"),
        question=_("Logging an avalanche or the day's conditions for other people."),
        cells=_cells(
            whiterisk=Cell(Support.YES, _("Avalanche observation form")),
            snowsafe=_UNKNOWN,
            clarity=_UNKNOWN,
            skitourenguru=_UNKNOWN,
            whympr=Cell(Support.YES, _("No login needed")),
            opensnow=_UNKNOWN,
            # apps/observations/ — FieldObservation, shown on the map.
            snowdesk=_YES,
        ),
    ),
)


def rows() -> list[tuple[Feature, list[Cell]]]:
    """Return every feature paired with its cells in :data:`PRODUCTS` order.

    The template renders a grid, and a grid needs its cells in column
    order — not a mapping it has to index by key, which Django's template
    language cannot do without a custom filter. Ordering here also means
    the header row and every body row are driven by the same tuple, so a
    column cannot slip out of alignment with its heading.

    Returns:
        One ``(feature, cells)`` pair per row, cells in column order.

    """
    return [
        (feature, [feature.cells[product.key] for product in PRODUCTS])
        for feature in FEATURES
    ]
