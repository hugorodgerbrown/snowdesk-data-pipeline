"""
apps/public/_component_fixtures.py — Synthetic context for the component library.

Hand-curated variant fixtures consumed by ``kind="components"`` panels in
the design-system page at ``/_components/``. Each component lists a
``VARIANTS`` tuple of context dicts ready to feed straight to its partial
via ``{% include partial with **variant.context %}``.

Lives outside ``design_tokens.py`` so the registry stays free of
data-construction logic — token panels iterate the registry, component
panels iterate these fixtures.

The leading underscore in the filename follows the project convention for
staff-only / internal modules, signalling that nothing in here is a public
import surface.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
from types import SimpleNamespace
from typing import Any

from django import forms

from apps.bulletins.services.day_summary import summary_for
from apps.public.guidance import load_field_guidance
from apps.public.templatetags.components import input_classes
from apps.weather.services.hourly_chart import build_hourly_chart
from apps.weather.services.weather_display import weather_icon_filename

# String constants for elevation bound type — mirror ``apps.public.views`` so the
# template filter (``elevation_icon``) sees the same strings without coupling
# this fixture module to the large ``views`` module.
_ELEVATION_LOWER = "LOWER"
_ELEVATION_UPPER = "UPPER"


@dataclasses.dataclass(frozen=True)
class _ElevationBounds:
    """Minimal elevation bounds stub for component-library fixtures.

    Mirrors the public-side :class:`~apps.public.views.ElevationBounds` just
    enough to satisfy the ``_rating_block.html`` template and the
    ``elevation_icon`` filter — without importing from the large
    ``apps.public.views`` module.

    Boolean-truthy when ``display`` is non-empty, matching the behaviour
    of the production dataclass.
    """

    lower: str
    upper: str
    display: str
    bound_type: str

    def __bool__(self) -> bool:
        """Return True when the bound has a displayable value."""
        return bool(self.display)


# Day-windows panel (SNOW-107) ---------------------------------------------
# Mirrors the dict shape produced by ``_build_day_windows`` in
# ``apps/public/views.py``. Labels and numbers below mirror ``_DANGER_PANEL_META``
# in the same module — copied verbatim so the fixture stays self-contained
# and doesn't reach into a non-public symbol. Pill labels mirror
# ``_DAY_WINDOW_PILL_LABELS`` (no rebadging here — we set them directly).

_DAY_WINDOW_LEVEL_META: dict[str, dict[str, str]] = {
    "low": {"label": "Low", "number": "1"},
    "moderate": {"label": "Moderate", "number": "2"},
    "considerable": {"label": "Considerable", "number": "3"},
    "high": {"label": "High", "number": "4"},
    "very_high": {"label": "Very high", "number": "5"},
}


def _make_window(
    period: str,
    level_key: str,
    pill_label: str,
    modifier: str = "",
    caption: str = "",
    bound_type: str = "",
) -> dict[str, Any]:
    """Build one day-window row dict for the component-library fixture.

    Pass ``bound_type`` (``"LOWER"`` = above a pivot, ``"UPPER"`` = below)
    for a banded row — it attaches an ``elevation_bounds`` namespace so the
    ``elevation_icon`` filter renders the mountain glyph (SNOW-298). Single
    rows leave it unset, matching ``_build_day_windows`` output.
    """
    meta = _DAY_WINDOW_LEVEL_META[level_key]
    row: dict[str, Any] = {
        "type": period,
        "level_key": level_key,
        "level_css": level_key.replace("_", "-"),
        "level_label": meta["label"],
        "level_number": f"{meta['number']}{modifier}",
        "caption": caption,
        "pill_label": pill_label,
    }
    if bound_type:
        # elevation_icon only reads .bound_type; a lightweight namespace
        # keeps the fixture self-contained (no import of the views dataclass).
        row["elevation_bounds"] = SimpleNamespace(bound_type=bound_type)
    return row


def _build_day_windows_variants() -> tuple[dict[str, Any], ...]:
    """Build the day-windows variant matrix.

    Seven stacked variants:

    * **All-day, level grid** — five synthetic ``all_day`` rows stepping
      ``low → very_high`` so tile + label contrast is reviewable across the
      whole EAWS scale on one screen.  Not a realistic bulletin (real
      bulletins have at most two windows) — a comparison harness.
    * **All-day with sublevel modifier** — one ``all_day`` row at
      considerable with a ``−`` modifier (badge reads ``3−``).
    * **Cross-category later** — ``all_day`` low + ``later`` moderate,
      the most common two-row shape in the bulletin sample.
    * **Within-category later** — ``all_day`` considerable−  + ``later``
      considerable (badge differential shows the intra-band rise).
    * **Numeric-pivot bands** — two ``all_day`` rows split by elevation,
      ``considerable`` below 2500 m and ``moderate`` above, each carrying the
      mountain elevation glyph. Represents Météo-France / ALBINA style
      banded bulletins (SNOW-298). Re-purposes the previous "MF elevation
      split" fixture.
    * **Treeline-pivot bands** — two ``all_day`` rows using "below treeline"
      and "above treeline" captions (ALBINA treeline-pivot style).
    * **Considerable below / high above bands** — higher-severity banded
      pair to exercise the orange → red tile colouring.
    """
    all_day_grid = [
        _make_window("all_day", "low", "All day"),
        _make_window("all_day", "moderate", "All day"),
        _make_window("all_day", "considerable", "All day"),
        _make_window("all_day", "high", "All day"),
        _make_window("all_day", "very_high", "All day"),
    ]
    all_day_sublevel = [
        _make_window("all_day", "considerable", "All day", modifier="-"),
    ]
    cross_category = [
        _make_window("all_day", "low", "All day"),
        _make_window("later", "moderate", "Later"),
    ]
    within_category = [
        _make_window("all_day", "considerable", "All day", modifier="-"),
        _make_window("later", "considerable", "Later"),
    ]
    # Numeric-pivot bands: considerable below 2500 m / moderate above 2500 m.
    # Re-purposed from the previous "MF elevation split" fixture. Lower band
    # first (below → UPPER bound), then upper band (above → LOWER bound).
    numeric_pivot_bands = [
        _make_window(
            "all_day",
            "considerable",
            "All day",
            caption="below 2500 m",
            bound_type="UPPER",
        ),
        _make_window(
            "all_day", "moderate", "All day", caption="above 2500 m", bound_type="LOWER"
        ),
    ]
    # Treeline-pivot bands: low below treeline / considerable above treeline.
    treeline_pivot_bands = [
        _make_window(
            "all_day", "low", "All day", caption="below treeline", bound_type="UPPER"
        ),
        _make_window(
            "all_day",
            "considerable",
            "All day",
            caption="above treeline",
            bound_type="LOWER",
        ),
    ]
    # Higher-severity pair: considerable below / high above.
    high_severity_bands = [
        _make_window(
            "all_day",
            "considerable",
            "All day",
            caption="below 2200 m",
            bound_type="UPPER",
        ),
        _make_window(
            "all_day", "high", "All day", caption="above 2200 m", bound_type="LOWER"
        ),
    ]
    return (
        {
            "caption": "All day · five EAWS levels",
            "context": {"day_windows": all_day_grid},
        },
        {
            "caption": "All day · sublevel modifier (3−)",
            "context": {"day_windows": all_day_sublevel},
        },
        {
            "caption": "Cross-category later · low → moderate",
            "context": {"day_windows": cross_category},
        },
        {
            "caption": "Within-category later · considerable− → considerable",
            "context": {"day_windows": within_category},
        },
        {
            "caption": "Numeric-pivot bands · considerable / moderate at 2500 m",
            "context": {"day_windows": numeric_pivot_bands},
        },
        {
            "caption": "Treeline bands · low below / considerable above treeline",
            "context": {"day_windows": treeline_pivot_bands},
        },
        {
            "caption": "High-severity bands · considerable below 2200 m / high above",
            "context": {"day_windows": high_severity_bands},
        },
    )


DAY_WINDOWS_VARIANTS: tuple[dict[str, Any], ...] = _build_day_windows_variants()


def _build_season_calendar_variants() -> tuple[dict[str, Any], ...]:
    """Build the season calendar demo variant for the component library.

    Constructs a synthetic 13-week SeasonGrid (Nov 2025 – Jan 2026) with
    hand-picked cells covering every cell state: no-rating, all five EAWS
    solid levels, split pairs (afternoon-elevated), time-split AM/PM pairs
    (SNOW-291), today, and selected.
    No database access — purely synthetic fixture data.

    Schedule entries can be:
    - A string key: ``"L"``, ``"M"``, ``"C"``, ``"H"``, ``"VH"``, ``"nr"``
      (uniform day — min == max).
    - A 2-tuple ``(min_key, max_key)`` for the diagonal-split (afternoon-elevated).
    - A 4-tuple ``(min_key, max_key, am_key, pm_key)`` for the vertical AM/PM
      time-split tile (SNOW-291).
    """
    from apps.public.season_calendar import SeasonCell, SeasonGrid

    _KEY = {
        "nr": "no_rating",
        "L": "low",
        "M": "moderate",
        "C": "considerable",
        "H": "high",
        "VH": "very_high",
    }
    _today = datetime.date(2026, 1, 20)
    _selected = datetime.date(2026, 1, 14)

    # Nov 3 2025 is a Monday — zero leading padding.
    _start = datetime.date(2025, 11, 3)

    # 13 weeks × 7 days.  Each entry is a short-code string (solid cell),
    # a (min, max) 2-tuple (diagonal split / afternoon-elevated cell), or
    # a (min, max, am, pm) 4-tuple for the AM/PM time-split tile (SNOW-291).
    _schedule: list[str | tuple[str, ...]] = [
        # Week 1  Nov 3–9    no data yet
        "nr",
        "nr",
        "nr",
        "nr",
        "nr",
        "nr",
        "nr",
        # Week 2  Nov 10–16  season opening
        "nr",
        "nr",
        "nr",
        "L",
        "L",
        "L",
        "L",
        # Week 3  Nov 17–23  low period
        "L",
        "L",
        "L",
        "L",
        "L",
        "L",
        "L",
        # Week 4  Nov 24–30  creeping up
        "L",
        "M",
        "M",
        "M",
        "M",
        "M",
        "L",
        # Week 5  Dec 1–7    considerable
        "M",
        "M",
        "C",
        "C",
        "C",
        "M",
        "M",
        # Week 6  Dec 8–14   high / very-high spike
        "C",
        "H",
        "H",
        "VH",
        "VH",
        "H",
        "C",
        # Week 7  Dec 15–21  diagonal + AM/PM time-split (SNOW-291)
        ("L", "M"),
        ("L", "C"),
        ("M", "C", "M", "M"),  # flat-but-split: M AM + M PM (SNOW-291)
        ("M", "H", "M", "H"),  # escalating time-split: M AM + H PM (SNOW-291)
        ("C", "H"),
        "C",
        "M",
        # Week 8  Dec 22–28  settling
        "M",
        "M",
        "L",
        "L",
        "M",
        "M",
        "C",
        # Week 9  Dec 29–Jan 4
        "M",
        ("L", "M"),
        "L",
        "L",
        ("L", "M"),
        "M",
        "M",
        # Week 10 Jan 5–11   low period
        "L",
        "L",
        "L",
        "L",
        "L",
        "L",
        "M",
        # Week 11 Jan 12–18  selected date (Jan 14) in this week
        "M",
        ("M", "C"),
        ("L", "C"),
        "L",
        "M",
        "M",
        "L",
        # Week 12 Jan 19–25  today (Jan 20) in this week
        ("L", "M"),
        "M",
        "M",
        "M",
        "L",
        "L",
        ("L", "M"),
        # Week 13 Jan 26–Feb 1
        "M",
        "M",
        "M",
        "L",
        "L",
        ("L", "M"),
        "M",
    ]

    month_parity = 0
    prev_month: int | None = None
    cells: list[SeasonCell] = []

    for i, entry in enumerate(_schedule):
        d = _start + datetime.timedelta(days=i)
        if prev_month is not None and d.month != prev_month:
            month_parity = 1 - month_parity
        prev_month = d.month

        am_key = ""
        pm_key = ""
        if isinstance(entry, tuple):
            min_key = _KEY[entry[0]]
            max_key = _KEY[entry[1]]
            if len(entry) == 4:
                # 4-tuple: AM/PM time-split (SNOW-291)
                am_key = _KEY[entry[2]]
                pm_key = _KEY[entry[3]]
        else:
            min_key = max_key = _KEY[entry]

        has_bulletin = min_key != "no_rating"
        cells.append(
            SeasonCell(
                date=d,
                min_rating_key=min_key,
                max_rating_key=max_key,
                subdivision="",
                has_bulletin=has_bulletin,
                is_today=d == _today,
                is_selected=d == _selected and d != _today,
                month_parity=month_parity,
                am_rating_key=am_key,
                pm_rating_key=pm_key,
            )
        )

    # Pack flat list into 7-row columns (start is Monday — no leading pad).
    columns: list[tuple[SeasonCell | None, ...]] = [
        tuple(cells[i : i + 7]) for i in range(0, len(cells), 7)
    ]

    # Build month labels: non-empty only on the first column of each month.
    _MONTH_ABBR = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    month_labels: list[str] = []
    last_month: int | None = None
    for column in columns:
        first = next((c for c in column if c is not None), None)
        if first is not None and first.date.month != last_month:
            month_labels.append(_MONTH_ABBR[first.date.month - 1])
            last_month = first.date.month
        else:
            month_labels.append("")

    grid = SeasonGrid(columns=columns, month_labels=month_labels, season_label="25/26")

    # ---- ALBINA elevation-only (2-band, all_day) — one synthetic cell ----
    _albina_eo_bands = [
        {
            "band_id": "above-2200",
            "label": "Above 2200 m",
            "rating_key": "considerable",
            "time_period": "all_day",
        },
        {
            "band_id": "below-2200",
            "label": "Below 2200 m",
            "rating_key": "low",
            "time_period": "all_day",
        },
    ]
    _albina_eo_cell = SeasonCell(
        date=datetime.date(2026, 1, 7),
        min_rating_key="low",
        max_rating_key="considerable",
        subdivision="",
        has_bulletin=True,
        source="ALBINA",
        bands=_albina_eo_bands,
    )
    _albina_eo_grid = SeasonGrid(
        columns=[(_albina_eo_cell, None, None, None, None, None, None)],
        month_labels=["Jan"],
        season_label="25/26",
    )

    # ---- ALBINA 2×2 (4-band: earlier+later × 2 elevation bands) -----------
    _albina_2x2_bands = [
        {
            "band_id": "above-2500",
            "label": "Above 2500 m",
            "rating_key": "considerable",
            "time_period": "earlier",
        },
        {
            "band_id": "below-2500",
            "label": "Below 2500 m",
            "rating_key": "low",
            "time_period": "earlier",
        },
        {
            "band_id": "above-2800",
            "label": "Above 2800 m",
            "rating_key": "high",
            "time_period": "later",
        },
        {
            "band_id": "below-2800",
            "label": "Below 2800 m",
            "rating_key": "moderate",
            "time_period": "later",
        },
    ]
    _albina_2x2_cell = SeasonCell(
        date=datetime.date(2026, 1, 8),
        min_rating_key="low",
        max_rating_key="high",
        subdivision="",
        has_bulletin=True,
        source="ALBINA",
        bands=_albina_2x2_bands,
    )
    _albina_2x2_grid = SeasonGrid(
        columns=[(_albina_2x2_cell, None, None, None, None, None, None)],
        month_labels=["Jan"],
        season_label="25/26",
    )

    return (
        {
            "caption": "Full season — all cell states",
            "context": {"season_calendar": grid},
        },
        {
            "caption": "ALBINA · elevation-only (2-band horizontal split)",
            "context": {"season_calendar": _albina_eo_grid},
        },
        {
            "caption": "ALBINA · elevation-time (2×2 four-quadrant grid)",
            "context": {"season_calendar": _albina_2x2_grid},
        },
    )


SEASON_CALENDAR_VARIANTS: tuple[dict[str, Any], ...] = _build_season_calendar_variants()


# Button variants (SNOW-200) ------------------------------------------------
# Covers all three visual variants × both sizes, plus a full-width example.

BUTTON_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Primary · standard",
        "context": {
            "label": "View a sample bulletin →",
            "href": "#",
            "variant": "primary",
            "size": "standard",
        },
    },
    {
        "caption": "Primary · compact",
        "context": {
            "label": "Subscribe",
            "variant": "primary",
            "size": "compact",
        },
    },
    {
        "caption": "Secondary · standard",
        "context": {
            "label": "Explore the map →",
            "href": "#",
            "variant": "secondary",
            "size": "standard",
        },
    },
    {
        "caption": "Secondary · compact",
        "context": {
            "label": "Explore the map",
            "href": "#",
            "variant": "secondary",
            "size": "compact",
        },
    },
    {
        "caption": "Ghost · standard",
        "context": {
            "label": "Sign in with a passkey",
            "variant": "ghost",
            "size": "standard",
        },
    },
    {
        "caption": "Ghost · compact",
        "context": {
            "label": "Add a passkey for this device",
            "variant": "ghost",
            "size": "compact",
        },
    },
    {
        "caption": "Destructive · compact",
        "context": {
            "label": "Delete account",
            "variant": "destructive",
            "size": "compact",
        },
    },
    {
        "caption": "Primary · full width",
        "context": {
            "label": "Yes, unsubscribe me",
            "variant": "primary",
            "size": "standard",
            "full_width": True,
        },
    },
)


# Card variants (SNOW-200) ---------------------------------------------------
# One per real-world padding value used across the codebase.

CHIP_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Default — neutral filled (category label, EAWS matrix chips)",
        "context": {
            "text": "Wind slab",
            "data_testid": "category-pill",
        },
    },
    {
        "caption": "variant=time — outlined ghost pill for time-period labels",
        "context": {
            "text": "All day",
            "variant": "time",
            "data_testid": "day-window-pill",
        },
    },
    {
        "caption": "variant=time · Later",
        "context": {
            "text": "Later",
            "variant": "time",
        },
    },
    {
        "caption": "variant=time · Morning",
        "context": {
            "text": "Morning",
            "variant": "time",
        },
    },
)


CARD_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "p-4 — compact passkey card",
        "context": {
            "title": "My MacBook Pro",
            "body": "Last used 14 January 2026",
            "padding": "p-4",
        },
    },
    {
        "caption": "px-5 py-4 — info banner / welcome card",
        "context": {
            "title": "Your subscription is confirmed.",
            "body": "You'll receive alerts for the regions below.",
            "padding": "px-5 py-4",
        },
    },
    {
        "caption": "p-6 — subscribe CTA card (default)",
        "context": {
            "title": "Get avalanche alerts",
            "body": (
                "Enter your email to receive daily bulletin updates for this region."
            ),
            "padding": "p-6",
        },
    },
    {
        "caption": "px-6 py-8 — empty-state card",
        "context": {
            "title": "You have no active subscriptions.",
            "body": "",
            "padding": "px-6 py-8",
            "center": True,
        },
    },
    {
        "caption": "p-8 — status page card (centred)",
        "context": {
            "title": "Check your inbox",
            "body": (
                "We've sent you a link to manage your subscriptions."
                " It expires in 24 hours."
            ),
            "padding": "p-8",
            "center": True,
        },
    },
)


# Status page variants (SNOW-200) -------------------------------------------
# Rendered via the _status_page_demo.html wrapper template.

STATUS_PAGE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "With CTA — link expired",
        "context": {
            "heading": "This link has expired",
            "body": (
                "Account links are only valid for 24 hours."
                " This one has expired or is invalid."
            ),
            "cta_label": "Request a new link",
            "cta_href": "#",
        },
    },
    {
        "caption": "Without CTA — check inbox",
        "context": {
            "heading": "Check your inbox",
            "body": (
                "If that address is registered, we've sent you a link to manage"
                " your subscriptions. It expires in 24 hours."
            ),
            "cta_label": None,
            "cta_href": None,
        },
    },
)


# Collapsible panel (SNOW-203) ------------------------------------------------
# Three variants exercising the _collapsible_panel.html partial:
#   1. Closed · single body — the default (closed) state with body_html.
#   2. Open · single body — same shape with is_open=True so the body
#      styling and slf-prose layout are visible without interaction.
#   3. Open · multi-entry tendency — body_template delegates to
#      _tendency_body.html; a synthetic ``prose`` dict with a two-entry
#      tendency list exercises the multi-day iteration path.
#
# The tendency_has_comment filter reads prose as a dict with a "tendency"
# key; each entry is a dict with a "comment" key.  The filter returns True
# when any entry has a non-empty comment, so both entries carry comment text.


def _build_collapsible_panel_variants() -> tuple[dict[str, Any], ...]:
    """Build the collapsible-panel variant matrix.

    Returns three variants: closed simple body, open simple body, and
    an open multi-entry tendency body rendered via ``_tendency_body.html``.
    """
    from django.utils.safestring import mark_safe

    simple_body = mark_safe(  # noqa: S308 — static fixture HTML, not user input
        "<p>Fresh snowfall overnight has loaded leeward slopes significantly. "
        "Weak layers from the cold spell earlier in the week remain buried "
        "beneath the new snow and are sensitive to additional loading.</p>"
    )

    tendency_prose = {
        "tendency": [
            {
                "comment": (
                    "<h1>Outlook for Friday</h1>"
                    "<p>Hazard will increase through the day as temperatures "
                    "rise above the freezing level. Wet-snow avalanches are "
                    "likely on solar aspects above 1800 m.</p>"
                )
            },
            {
                "comment": (
                    "<h1>Outlook for Saturday</h1>"
                    "<p>Conditions improve with a return to colder, clearer "
                    "weather. Danger will consolidate at Moderate (2) across "
                    "all elevations.</p>"
                )
            },
        ]
    }

    return (
        {
            "caption": "Closed · single body",
            "context": {
                "title": "Snowpack structure",
                "data_testid": "snowpack-panel",
                "body_html": simple_body,
                "is_open": False,
            },
        },
        {
            "caption": "Open · single body",
            "context": {
                "title": "Snowpack structure",
                "data_testid": "snowpack-panel",
                "body_html": simple_body,
                "is_open": True,
            },
        },
        {
            "caption": "Open · multi-entry tendency",
            "context": {
                "title": "Outlook for Friday",
                "data_testid": "tendency-panel",
                "body_template": "includes/_tendency_body.html",
                "is_open": True,
                "prose": tendency_prose,
            },
        },
    )


COLLAPSIBLE_PANEL_VARIANTS: tuple[dict[str, Any], ...] = (
    _build_collapsible_panel_variants()
)


# ── Form field (SNOW-672) ───────────────────────────────────────────────────
# A real Django form rather than a SimpleNamespace, because the partial
# renders the widget: ``{{ field }}`` has to produce an <input>, not a repr.
# The errored variant is bound to invalid data so the error list is the one
# Django actually produces.


class _DemoFieldForm(forms.Form):
    """Throwaway form supplying one rendered field to the component library."""

    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(
            attrs={"class": input_classes(), "placeholder": "your@email.com"}
        ),
    )


_DEMO_FIELD = _DemoFieldForm()["email"]
_DEMO_FIELD_WITH_ERROR = _DemoFieldForm(data={"email": "not-an-email"})["email"]

FORM_FIELD_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Required field",
        "context": {"field": _DEMO_FIELD, "label": "Email address"},
    },
    {
        "caption": "Optional field",
        "context": {"field": _DEMO_FIELD, "label": "Name", "optional": True},
    },
    {
        "caption": "With a validation error",
        "context": {"field": _DEMO_FIELD_WITH_ERROR, "label": "Email address"},
    },
)

PAGE_TITLE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Default — body starts straight after",
        "context": {"text": "Colophon", "data_testid": "colophon-heading"},
    },
    {
        "caption": "Tighter — a subtitle line follows",
        "context": {"text": "Privacy Policy", "class_extra": "mb-2"},
    },
    {
        "caption": "No spacing — the wrapper owns it",
        "context": {"text": "Verbier", "class_extra": "mb-0"},
    },
)

EYEBROW_VARIANTS: tuple[dict[str, Any], ...] = (
    {"caption": "Bulletin section heading", "context": {"text": "Day Risk Profile"}},
    {"caption": "Bulletin section heading", "context": {"text": "Avalanche Problems"}},
    {"caption": "Bulletin section heading", "context": {"text": "Snowpack & Weather"}},
    {
        "caption": "Library foundation label",
        "context": {"text": "foundation", "tag": "p"},
    },
    {"caption": "Light theme heading", "context": {"text": "Light", "tag": "h3"}},
    {"caption": "Dark theme heading", "context": {"text": "Dark", "tag": "h3"}},
    {
        "caption": "Staff design-system eyebrow",
        "context": {"text": "staff · design system", "tag": "p", "class_extra": "mb-1"},
    },
)

META_CELL_VARIANTS: tuple[dict[str, Any], ...] = (
    {"caption": "Issued", "context": {"text": "Issued"}},
    {"caption": "Valid until", "context": {"text": "Valid until"}},
    {"caption": "Next update", "context": {"text": "Next update"}},
)

# Resort meta row (SNOW-501) --------------------------------------------------
# Populated value, plus both blank-placeholder wordings (public em-dash vs
# staff curation hint) — same context shape _resort_popup.html feeds per row.
RESORT_META_ROW_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Populated value",
        "context": {
            "label": "Operator",
            "value": "Téléverbier SA",
            "is_staff": False,
            "staff_hint": "Add operator name",
        },
    },
    {
        "caption": "Blank — public placeholder",
        "context": {
            "label": "Operator",
            "value": "",
            "is_staff": False,
            "staff_hint": "Add operator name",
        },
    },
    {
        "caption": "Blank — staff curation hint",
        "context": {
            "label": "Operator",
            "value": "",
            "is_staff": True,
            "staff_hint": "Add operator name",
        },
    },
)

# Resort "why it matters" line (SNOW-542) -------------------------------------
# The curated prose line, plus all three blank branches. The partial reads
# ``resort`` directly, so the fixtures pass a stand-in object rather than a
# flat context — ``SimpleNamespace`` is enough for the two attributes used.
RESORT_WHY_IT_MATTERS_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Curated line",
        "context": {
            "resort": SimpleNamespace(
                name="Tschiertschen",
                why_it_matters=(
                    "Nationally known freeride destination. Four lifts, "
                    "disproportionate avalanche relevance."
                ),
            ),
            "is_staff": False,
            "can_favourite": True,
        },
    },
    {
        "caption": "Blank — staff curation hint",
        "context": {
            "resort": SimpleNamespace(name="Haldigrat", why_it_matters=""),
            "is_staff": True,
            "can_favourite": True,
        },
    },
    {
        "caption": "Blank — anonymous register prompt",
        "context": {
            "resort": SimpleNamespace(name="Haldigrat", why_it_matters=""),
            "is_staff": False,
            "can_favourite": False,
        },
    },
    {
        "caption": "Blank — signed in, renders nothing",
        "context": {
            "resort": SimpleNamespace(name="Haldigrat", why_it_matters=""),
            "is_staff": False,
            "can_favourite": True,
        },
    },
)


# Resort facts block (SNOW-695) -----------------------------------------------
# The curated Resort columns the detail page stored but never rendered. The
# partial reads ``resort`` directly, so — like the why-it-matters fixtures
# above — a ``SimpleNamespace`` stand-in carries just the attributes used.
# Three variants cover the whole contract: fully curated, partially curated
# (including a base elevation with no top, and a season open with no close),
# and nothing curated at all, which must render no container rather than an
# empty one.
RESORT_FACTS_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Fully curated",
        "solo": True,
        "context": {
            "resort": SimpleNamespace(
                operator_name="Zermatt Bergbahnen AG",
                website="https://www.zermatt.ch/",
                notes=(
                    "Lift-served access to the Theodul glacier; the "
                    "Hohtälli and Rothorn sectors open onto committing "
                    "north-facing terrain."
                ),
                num_lifts=34,
                num_runs=53,
                total_piste_km=196.5,
                base_elevation_m=1620,
                top_elevation_m=3899,
                typical_season_open="11-23",
                typical_season_close="04-27",
            ),
        },
    },
    {
        "caption": "Partially curated — one-sided elevation and season",
        "solo": True,
        "context": {
            "resort": SimpleNamespace(
                operator_name="",
                website="",
                notes="",
                num_lifts=4,
                num_runs=None,
                total_piste_km=None,
                base_elevation_m=1343,
                top_elevation_m=None,
                typical_season_open="12-14",
                typical_season_close="",
            ),
        },
    },
    {
        "caption": "Nothing curated — renders no container at all",
        "solo": True,
        "context": {
            "resort": SimpleNamespace(
                operator_name="",
                website="",
                notes="",
                num_lifts=None,
                num_runs=None,
                total_piste_km=None,
                base_elevation_m=None,
                top_elevation_m=None,
                typical_season_open="",
                typical_season_close="",
            ),
        },
    },
)

# Overlay primitives (SNOW-486) -----------------------------------------------
# The four consolidated overlay shapes — banner, modal, sheet — each with a
# "static" context flag so the component library renders them inline rather
# than pinned to the real viewport edge. (Toast's own fixtures sit above,
# pre-dating this consolidation.)

OVERLAY_BANNER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Strip — off-season notice",
        "context": {
            "variant": "strip",
            "body": "Archive bulletins are shown outside the winter season.",
            "icon": "calendar",
            "static": True,
        },
    },
    {
        # SNOW-639: `dismissible` used to be honoured by the floating branch
        # only, so a strip caller passing it got no × and no complaint. Both
        # strip states are shown side by side here so that asymmetry cannot
        # come back unnoticed.
        "caption": "Strip — dismissible (off-season bar)",
        "context": {
            "variant": "strip",
            "body": "Archive bulletins are shown outside the winter season.",
            "icon": "calendar",
            "dismissible": True,
            "static": True,
        },
    },
    {
        "caption": "Floating — no dismiss, no CTA",
        "context": {
            "variant": "floating",
            "body": "Checking for updates…",
            "static": True,
        },
    },
    {
        "caption": "Floating — dismissible, no CTA",
        "context": {
            "variant": "floating",
            "icon": "refresh",
            "title": "You're offline",
            "body": "Showing the last cached bulletin.",
            "title_id": "component-library-banner-title-1",
            "dismissible": True,
            "static": True,
        },
    },
    {
        "caption": "Floating — dismissible with CTA (SW update shape)",
        "context": {
            "variant": "floating",
            "icon": "refresh",
            "title": "Update available",
            "body": "A new version of Snowdesk is ready.",
            "title_id": "component-library-banner-title-2",
            # SNOW-586: body_id — a caller-writable hook for a body line JS
            # rewrites at runtime (the basemap-download whole-area-eviction
            # confirm banner injects a dynamic area-name list here).
            "body_id": "component-library-banner-body-2",
            "cta_id": "component-library-banner-cta",
            "cta_label": "Reload",
            "dismissible": True,
            "static": True,
        },
    },
    {
        "caption": "Floating — off-map nudge (top, dismissible)",
        "context": {
            "variant": "floating",
            "icon": "location-off",
            "position": "top",
            "title": "Off the map",
            "body": "Your location is outside the mapped regions.",
            "title_id": "component-library-banner-title-3",
            "dismissible": True,
            "static": True,
        },
    },
)


OVERLAY_MODAL_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Forced update (PWA shape)",
        "context": {
            "id": "component-library-overlay-modal",
            "title_id": "component-library-overlay-modal-title",
            "body_id": "component-library-overlay-modal-body",
            "title": "Update required",
            "body": "A new version of Snowdesk is available. Reload to continue.",
            "cta_id": "component-library-overlay-modal-cta",
            "cta_label": "Reload",
            "static": True,
        },
    },
)


OVERLAY_SHEET_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Favourite / report sheet shell",
        "context": {
            "id": "component-library-overlay-sheet",
            "aria_label": "Favourite",
            "static": True,
        },
    },
)


# Sheet header (SNOW-474; the 44×44 × and title_class override SNOW-645
# review) --------------------------------------------------------------------
# Shared title + persistent × close control for the favourites/report/
# downloads map sheets. Both title and close_action are required — supplying
# them here keeps the include from erroring on a missing var.

SHEET_HEADER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Report sheet",
        "context": {"title": "Report", "close_action": "dismiss"},
    },
    {
        "caption": "Favourite sheet",
        "context": {"title": "Favourite", "close_action": "dismiss"},
    },
    {
        "caption": "Manage downloads sheet (title_class override)",
        "context": {
            "title": "Your downloads",
            "close_action": "dismiss",
            "title_class": "text-lg font-bold",
        },
    },
)


# Switch (SNOW-645) -----------------------------------------------------------
# First use: the "Manage downloads" sheet's map-overlay control (labelled
# "Display on the map" on every UGC panel since SNOW-658).
# Pure CSS (Tailwind's peer variant) — no JS runs on this page, so both
# states render correctly from the `checked` attribute alone.

SWITCH_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Off",
        "context": {"id": "component-library-switch-off"},
    },
    {
        "caption": "On",
        "context": {"id": "component-library-switch-on", "checked": True},
    },
    {
        # SNOW-645 review: includes/_switch.html's own wrapping <label>
        # carries has-[:disabled]:cursor-not-allowed/opacity-50, driven by
        # the :disabled pseudo-class on the real <input> nested inside it —
        # a state this fixture can render statically (unlike :focus-visible,
        # which needs real interaction) via the typed `disabled` boolean.
        "caption": "Disabled",
        "context": {
            "id": "component-library-switch-disabled",
            "disabled": True,
        },
    },
)


# Theme preference -------------------------------------------------------------
# The settings page's light/dark/system radio group. It takes no parameters, so
# there is exactly one variant — the library entry exists to make the control
# discoverable ("reuse first"), not to show off states.
#
# The rendered variant always shows "System" selected, because that is what the
# server sends: the real preference is in localStorage and
# static/js/theme_preference.js checks the matching radio on load. The library
# renders the partial without that script, so what appears here is the
# no-JavaScript state, which is the honest thing to show.
THEME_PREFERENCE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Default",
        "context": {},
    },
)


# Map overlay toggle (SNOW-658) ------------------------------------------------
# The "Show X on the map" footer panel shared by the three map sheets
# (downloads, favourites, field observations). Pure markup — the owning JS
# module binds the switch by id, and none of that JS runs on this page, so
# both variants render exactly as a sheet paints them on open.

MAP_OVERLAY_TOGGLE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        # SNOW-658: one string on all three panels now — "Show areas on
        # the map" / "Show favourites on the map" / "Show community
        # reports on the map" were three sentences for one control, and
        # includes/_ugc_panel.html fixes the replacement rather than
        # passing it in. The partial keeps its `label` parameter: it is a
        # generic control, and this page is where its shape is reviewed.
        "caption": "As every UGC panel renders it",
        "context": {
            "id": "component-library-map-overlay-toggle-downloads",
            "label": "Display on the map",
        },
    },
    {
        "caption": "A longer label, to check the switch holds its size",
        "context": {
            "id": "component-library-map-overlay-toggle-favourites",
            "label": "Display the community's field observations on the map",
        },
    },
)


# Overflow menu (SNOW-645) -----------------------------------------------------
# First use: the "Manage downloads" sheet's per-row Rename/Remove actions.
# `open=True` on one variant so the menu's contents are visible without
# overflow_menu.js running — the component library page loads no
# interaction JS for any partial (see that module's own header).

OVERFLOW_MENU_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Closed",
        "context": {
            "trigger_id": "component-library-overflow-trigger-closed",
            "menu_id": "component-library-overflow-menu-closed",
            "trigger_label": "More actions",
            "items": [
                {"label": "Rename"},
                {"label": "Remove"},
            ],
        },
    },
    {
        "caption": "Open — custom area (Rename + Remove)",
        "context": {
            "trigger_id": "component-library-overflow-trigger-open",
            "menu_id": "component-library-overflow-menu-open",
            "trigger_label": "More actions",
            "items": [
                {"label": "Rename"},
                {"label": "Remove"},
            ],
            "open": True,
        },
    },
    {
        "caption": "Open — region (Remove only)",
        "context": {
            "trigger_id": "component-library-overflow-trigger-region",
            "menu_id": "component-library-overflow-menu-region",
            "trigger_label": "More actions",
            "items": [
                {"label": "Remove"},
            ],
            "open": True,
        },
    },
)


# UGC panel + row (SNOW-658) ---------------------------------------------------
# The skeleton and row shape shared by the three map panels that manage a
# user's own data — downloads, favourites, field observations. Hugo's "Map
# panels — common format" design: five shell parts in a fixed order, one
# row anatomy of five slots.
#
# `rows_template`, `icon_template` and `actions_template` all take a
# template PATH because Django has no slot mechanism (see
# includes/_ugc_panel.html's own header). The library hands the panel a
# demo template rendering two static rows — every real caller's list
# either loads over HTMX or is filled by JS, and this page runs neither.
#
# There is no `toggle_label`: the footer switch reads "Display on the map"
# on all three panels, so the shared partial owns the string.

UGC_PANEL_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Favourites panel (no header extra)",
        "context": {
            "title": "Favourites",
            "icon_template": "includes/_icon_favourite.html",
            "context_line": "Favourites are private and not shared.",
            "section_label": "Places",
            "rows_template": "public/partials/_ugc_panel_demo_rows.html",
            "cta_label": "Add a favourite",
            "toggle_id": "component-library-ugc-panel-toggle-favourites",
        },
    },
    {
        "caption": "Downloads panel (budget block in the header slot)",
        "context": {
            "title": "Your downloads",
            "icon_template": "includes/_icon_downloads.html",
            "context_line": (
                "Your areas follow your account. The map data and the budget "
                "stay on this device."
            ),
            # No section_label: this panel groups its rows under two
            # headings of its own, rendered from its rows template.
            "header_template": "public/partials/_map_downloads_header.html",
            "rows_template": "public/partials/_ugc_panel_demo_rows.html",
            "cta_label": "Download a custom area",
            "toggle_id": "component-library-ugc-panel-toggle-downloads",
        },
    },
)


UGC_PANEL_ROW_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        # Every variant here takes the downloads panel's actions template,
        # which is the only one that renders without a model instance in
        # context. It is also the fullest: a pencil and a trash. A panel
        # with one action renders the same row minus the pencil.
        "caption": "Plain row — label, meta line, actions",
        "context": {
            "label": "Arolla ridge",
            "meta": "Val d'Hérens",
            "actions_template": "includes/_map_downloads_row_actions.html",
        },
    },
    {
        # The caption said "the label itself is the edit target" until the
        # label grew a job of its own (below). It never was: SNOW-658 took
        # rename off the label and left it on the pencil, which is the
        # control this variant actually renders.
        "caption": "Renameable row — the pencil opens an editor over the label",
        "context": {
            "label": "Cabane des Dix",
            "meta": "Val des Dix",
            "renameable": True,
            "rename_label": "Favourite name",
            "actions_template": "includes/_map_downloads_row_actions.html",
        },
    },
    {
        # The map panels' rows only. Pressing the name frames that place —
        # a route's bbox, a pin's coordinates — which is why the name is a
        # real button here and inert text in every variant above: the
        # difference is visible at rest as the hover fill and the focus
        # ring a `<span>` cannot take.
        "caption": "Row whose name frames its place on the map",
        "context": {
            "label": "Lac de Vaux hike",
            "meta": "8.4 km · 645 m ascent · 525 m descent",
            "focus_target": "7.231800,46.089100,7.290300,46.120400",
            "focus_label": "Zoom to Lac de Vaux hike",
            "actions_template": "includes/_map_downloads_row_actions.html",
        },
    },
    {
        "caption": "Downloads row — colour rule and trailing measured value",
        "context": {
            "label": "Verbier",
            "meta": "Snowdesk Terrain",
            "value": "18.2 MB",
            "rule": True,
            "actions_template": "includes/_map_downloads_row_actions.html",
        },
    },
    {
        # SNOW-711. The account page's favourite row is the only one with
        # a disclosure: the map panels reach a pin's detail by tapping the
        # pin, which is a control they already have and this page has not.
        "caption": "Row with a trailing disclosure — expands under itself",
        "context": {
            "label": "Cabane des Dix",
            "meta": "Val des Dix · saved 3 Feb 2026",
            "actions_template": "includes/_map_downloads_row_actions.html",
            "disclosure_template": "includes/_row_disclosure.html",
            "disclosure_href": "#",
            "disclosure_panel_id": "component-library-row-disclosure-panel",
            "disclosure_label": "Show details for Cabane des Dix",
        },
    },
)


# Row disclosure (SNOW-711) ----------------------------------------------------
# The UGC row's trailing expand control, rendered on its own so the glyph
# and its 44×44 target are visible outside a row. Both states are shown:
# `aria-expanded` is flipped by the owning surface's module, and the
# chevron's rotation is a `group-aria-expanded:` variant of that one
# attribute, so a broken rotation is a broken state read.

ROW_DISCLOSURE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Collapsed — the state every row renders in",
        "context": {
            "disclosure_href": "#",
            "disclosure_hx_get": "#",
            "disclosure_panel_id": "component-library-disclosure-collapsed",
            "disclosure_label": "Show details for Cabane des Dix",
        },
    },
    {
        # Rendered by the library only. A real row starts collapsed and is
        # expanded by its module, which this page does not load.
        "caption": "Expanded — chevron rotated by aria-expanded",
        "context": {
            "disclosure_href": "#",
            "disclosure_hx_get": "#",
            "disclosure_panel_id": "component-library-disclosure-expanded",
            "disclosure_label": "Hide details for Cabane des Dix",
            "disclosure_expanded": True,
        },
    },
)


TOAST_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Error (HTMX banner shape) — no CTA, not dismissible",
        "context": {
            "kind": "error",
            "body": "Something went wrong.",
            "dismissible": False,
            "static": True,
        },
    },
    {
        "caption": "Info (SW-update shape) — CTA + dismiss",
        "context": {
            "kind": "info",
            "body": "An updated version is available.",
            "cta_label": "Reload",
            "cta_href": "#",
            "dismissible": True,
            "static": True,
        },
    },
    {
        "caption": "Warning with CTA",
        "context": {
            "kind": "warning",
            "body": "Your session is about to expire.",
            "cta_label": "Extend",
            "cta_href": "#",
            "dismissible": True,
            "static": True,
        },
    },
    {
        "caption": "Success — dismissible only",
        "context": {
            "kind": "success",
            "body": "Subscription saved.",
            "dismissible": True,
            "static": True,
        },
    },
    {
        "caption": "Info — non-dismissible",
        "context": {
            "kind": "info",
            "body": "Read-only mode is active.",
            "dismissible": False,
            "static": True,
        },
    },
)


TOAST_BANNER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Default copy",
        "context": {"static": True},
    },
    {
        "caption": "Custom body",
        "context": {
            "body": "3 changes couldn't be saved and won't be retried.",
            "static": True,
        },
    },
)


CALLOUT_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Warning",
        "context": {
            "kind": "warning",
            "heading": "We couldn't process this bulletin.",
            "body": (
                "We are sorry for the inconvenience. Please try again later"
                " or check back for an updated bulletin."
            ),
        },
    },
    {
        "caption": "Error",
        "context": {
            "kind": "error",
            "heading": "Something went wrong.",
            "body": "An unexpected error occurred. Please try again.",
        },
    },
    {
        "caption": "Success",
        "context": {
            "kind": "success",
            "heading": "Bulletin processed successfully.",
            "body": "The bulletin has been ingested and is ready to view.",
        },
    },
    {
        "caption": "Info",
        "context": {
            "kind": "info",
            "heading": "Bulletin updated.",
            "body": "A revised bulletin has been issued for this region.",
        },
    },
    {
        "caption": "Warning with diagnostic",
        "context": {
            "kind": "warning",
            "heading": "We couldn't process this bulletin.",
            "body": (
                'Bulletin ID: <span class="font-mono">'
                "CH-1234.2026-02-14T00:00:00+00:00</span>"
            ),
            "code_block": (
                "RenderModelBuildError: missing required field"
                " 'dangerRatings' at path $.properties"
            ),
            "cta_label": "Inspect in admin →",
            "cta_href": "/admin/bulletins/bulletin/42/change/",
        },
    },
)


# One variant per movement in the day-summary matrix, so the component
# library shows the real range rather than three static days. The explainer
# is generated by ``summary_for`` rather than transcribed, so a copy edit in
# the matrix can never leave this page quoting text the site no longer says.

DAY_CHARACTER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Static day — buried problem",
        "context": {
            "day_character": {
                "label": "Hard-to-read day",
                "explainer": summary_for("static", 3, ["persistent_weak_layers"]),
            }
        },
    },
    {
        "caption": "Static day — surface problem",
        "context": {
            "day_character": {
                "label": "Manageable day",
                "explainer": summary_for("static", 2, ["wind_slab"]),
            }
        },
    },
    {
        "caption": "Deteriorating day",
        "context": {
            "day_character": {
                "label": "Hard-to-read day",
                "explainer": summary_for(
                    "rising",
                    3,
                    ["persistent_weak_layers", "wet_snow"],
                    from_level=2,
                ),
            }
        },
    },
    {
        "caption": "Easing day — the problem swaps",
        "context": {
            "day_character": {
                "label": "Hard-to-read day",
                "explainer": summary_for(
                    "easing",
                    2,
                    ["persistent_weak_layers", "wet_snow"],
                    from_level=3,
                ),
            }
        },
    },
    {
        "caption": "Level holds, problem shifts",
        "context": {
            "day_character": {
                "label": "Manageable day",
                "explainer": summary_for("shifting", 2, ["wind_slab", "wet_snow"]),
            }
        },
    },
    {
        "caption": "Quiet day — no problem named",
        "context": {
            "day_character": {
                "label": "Stable day",
                "explainer": summary_for("static", 1, []),
            }
        },
    },
)


# ── Tendency outlook (SNOW-296) ─────────────────────────────────────────────
# One variant per canonical tendency_type (steady / increasing / decreasing),
# plus one neutral fallback variant (unknown type).  Each context dict
# contains an ``outlook`` key shaped like a TendencyOutlook instance —
# matching the ``with outlook=panel.tendency_outlook only`` include call.

TENDENCY_OUTLOOK_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Steady — constant danger",
        "context": {
            "outlook": SimpleNamespace(
                tendency_type="steady",
                arrow="→",
                label="Constant avalanche danger",
                valid_until="2026-04-16T23:59:59+00:00",
                highlights="Conditions remain broadly stable.",
            ),
        },
    },
    {
        "caption": "Increasing danger",
        "context": {
            "outlook": SimpleNamespace(
                tendency_type="increasing",
                arrow="↗",
                label="Increasing avalanche danger",
                valid_until="2026-04-16T23:59:59+00:00",
                highlights="Fresh snow and wind will increase the hazard tomorrow.",
            ),
        },
    },
    {
        "caption": "Decreasing danger",
        "context": {
            "outlook": SimpleNamespace(
                tendency_type="decreasing",
                arrow="↘",
                label="Decreasing avalanche danger",
                valid_until="2026-04-16T23:59:59+00:00",
                highlights="",
            ),
        },
    },
    {
        "caption": "Neutral fallback — unknown tendency_type",
        "context": {
            "outlook": SimpleNamespace(
                tendency_type="unknown_future_value",
                arrow="",
                label="Avalanche danger outlook",
                valid_until="2026-04-16T23:59:59+00:00",
                highlights="",
            ),
        },
    },
)


# ── Nav (SNOW-201) ──────────────────────────────────────────────────────────
# Covers the four meaningful states of the persistent top bar:
# bare logo, back-link variant, season-trigger variant, and authed subscriber.
# ``request.user`` and ``nav_subscriptions`` are overridden via SimpleNamespace
# so the partial's auth-area branches can be exercised without touching the
# context processor.

_NAV_REGION = SimpleNamespace(region_id="CH-VS-3431", name="Bex-Villars")

NAV_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Bare — logo only, unauthenticated",
        "context": {
            "request": SimpleNamespace(
                user=SimpleNamespace(
                    is_authenticated=False,
                    is_staff=False,
                    email="",
                ),
                csp_nonce="",
            ),
            "nav_subscriptions": [],
        },
    },
    {
        "caption": "With back link",
        "context": {
            "back_url": "/regions/CH-VS-3431/",
            "back_label": "Back to bulletin",
            "request": SimpleNamespace(
                user=SimpleNamespace(
                    is_authenticated=False,
                    is_staff=False,
                    email="",
                ),
                csp_nonce="",
            ),
            "nav_subscriptions": [],
        },
    },
    {
        "caption": "With season trigger",
        "context": {
            "season_trigger": SimpleNamespace(season_label="25/26"),
            "request": SimpleNamespace(
                user=SimpleNamespace(
                    is_authenticated=False,
                    is_staff=False,
                    email="",
                ),
                csp_nonce="",
            ),
            "nav_subscriptions": [],
        },
    },
    {
        "caption": "Authenticated subscriber",
        "context": {
            "request": SimpleNamespace(
                user=SimpleNamespace(
                    is_authenticated=True,
                    is_staff=False,
                    email="alice@example.com",
                ),
                csp_nonce="",
            ),
            "nav_subscriptions": [
                SimpleNamespace(region=_NAV_REGION),
            ],
        },
    },
)


# ── Site footer (SNOW-201) ──────────────────────────────────────────────────
# The footer reverses URLs internally and reads no context variables, so a
# single empty-context variant covers all states.

SITE_FOOTER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Default",
        "context": {},
    },
)


# ── Rating block (SNOW-201) ─────────────────────────────────────────────────
# Seven cards: one per EAWS problem type at a representative danger level,
# plus a prose-only card exercising the no-core-zone branch.
# Shapes match the dict produced by ``build_problem_cards`` / the render
# model traits — keys consumed by ``_rating_block.html``.


def _make_rating_card(
    category: str,
    danger_level: int,
    danger_level_key: str,
    problem_type: str,
    time_period: str,
    aspects: list[str],
    elevation: _ElevationBounds,
    label: str,
    time_period_label: str,
    core_zone_text: str,
    comment_html: str = "",
    panel_title: str = "",
    title_time_suffix: str = "",
    subdivision: str = "",
    subdivision_label: str = "",
    avalanche_type: str | None = None,
    avalanche_size: int | None = None,
    frequency_label: str | None = None,
    stability_label: str | None = None,
    danger_patterns: list[dict[str, str]] | None = None,
    prose_mentions_spatial: bool = False,
    field_guidance: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build one rating-block card dict in the shape ``_rating_block.html`` expects."""
    return {
        "category": category,
        "danger_level": danger_level,
        "danger_level_key": danger_level_key,
        "problem_type": problem_type,
        "time_period": time_period,
        "panel_title": panel_title,
        "title_time_suffix": title_time_suffix,
        "subdivision": subdivision,
        "subdivision_label": subdivision_label,
        "aspects": aspects,
        "elevation": elevation,
        "comment_html": comment_html,
        "label": label,
        "time_period_label": time_period_label,
        "hide_comment": False,
        "core_zone_text": core_zone_text,
        "avalanche_type": avalanche_type,
        "avalanche_size": avalanche_size,
        "frequency_label": frequency_label,
        "stability_label": stability_label,
        "danger_patterns": danger_patterns or [],
        "prose_mentions_spatial": prose_mentions_spatial,
        # SNOW-673. Empty on most variants on purpose: the library should
        # show the with-guidance and without-guidance cards side by side,
        # because "renders nothing when the problem type has no entry" is
        # half of the behaviour.
        "field_guidance": field_guidance or [],
    }


# Read the shipped notes rather than restating them, so the library cannot
# show copy the page does not (SNOW-673). ``cornices`` deliberately has no
# entry — that variant is the "renders nothing" half of the behaviour.
_FIELD_GUIDANCE = load_field_guidance()

RATING_BLOCK_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "New snow · moderate",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="new_snow",
                time_period="all_day",
                aspects=["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
                elevation=_ElevationBounds(
                    lower="1600",
                    upper="",
                    display="above 1600m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="New snow",
                time_period_label="",
                title_time_suffix="all day",
                core_zone_text="All aspects, above 1600m",
            ),
        },
    },
    {
        "caption": "Wind slab · considerable",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=3,
                danger_level_key="considerable",
                problem_type="wind_slab",
                time_period="all_day",
                aspects=["N", "NE", "NW"],
                elevation=_ElevationBounds(
                    lower="2400",
                    upper="",
                    display="above 2400m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Wind slab",
                time_period_label="",
                core_zone_text="N to NW aspects, above 2400m",
            ),
        },
    },
    {
        "caption": "Persistent weak layers · considerable",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=3,
                danger_level_key="considerable",
                problem_type="persistent_weak_layers",
                time_period="all_day",
                aspects=["N", "NE", "NW", "E"],
                elevation=_ElevationBounds(
                    lower="2600",
                    upper="",
                    display="above 2600m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Persistent weak layers",
                time_period_label="",
                core_zone_text="N to E aspects, above 2600m",
                field_guidance=[
                    {
                        "label": "Persistent weak layers",
                        "text": _FIELD_GUIDANCE["persistent_weak_layers"],
                    }
                ],
            ),
        },
    },
    {
        "caption": "Cornices · moderate",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="cornices",
                time_period="all_day",
                aspects=["N", "NE", "E", "NW"],
                elevation=_ElevationBounds(
                    lower="2200",
                    upper="",
                    display="above 2200m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Cornices",
                time_period_label="",
                core_zone_text="N to E and NW aspects, above 2200m",
            ),
        },
    },
    {
        "caption": "Wet snow · moderate · later",
        "context": {
            "card": _make_rating_card(
                category="wet",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="wet_snow",
                time_period="later",
                aspects=["E", "SE", "S", "SW", "W"],
                elevation=_ElevationBounds(
                    lower="",
                    upper="2200",
                    display="below 2200m",
                    bound_type=_ELEVATION_UPPER,
                ),
                label="Wet snow",
                time_period_label="Later",
                core_zone_text="E to W aspects, below 2200m",
                field_guidance=[
                    {"label": "Wet snow", "text": _FIELD_GUIDANCE["wet_snow"]}
                ],
            ),
        },
    },
    {
        "caption": "Gliding snow · moderate",
        "context": {
            "card": _make_rating_card(
                category="wet",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="gliding_snow",
                time_period="all_day",
                aspects=["S", "SE", "SW"],
                elevation=_ElevationBounds(
                    lower="",
                    upper="1800",
                    display="below 1800m",
                    bound_type=_ELEVATION_UPPER,
                ),
                label="Gliding snow",
                time_period_label="",
                core_zone_text="S-facing aspects, below 1800m",
            ),
        },
    },
    {
        "caption": "Wet snow · prose mentions scope",
        "context": {
            "card": _make_rating_card(
                category="wet",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="wet_snow",
                time_period="all_day",
                aspects=[],
                elevation=_ElevationBounds(
                    lower="",
                    upper="",
                    display="",
                    bound_type="",
                ),
                label="Wet snow",
                time_period_label="",
                core_zone_text="",
                prose_mentions_spatial=True,
                comment_html=(
                    "<p>Wet-snow slides likely on steep north-facing slopes "
                    "between approximately 2000 and 2400 m.</p>"
                ),
            ),
        },
    },
    {
        "caption": "Wet snow · no spatial scope",
        "context": {
            "card": _make_rating_card(
                category="wet",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="wet_snow",
                time_period="all_day",
                aspects=[],
                elevation=_ElevationBounds(
                    lower="",
                    upper="",
                    display="",
                    bound_type="",
                ),
                label="Wet snow",
                time_period_label="",
                core_zone_text="",
                prose_mentions_spatial=False,
                comment_html="<p>Moist snow slides expected as the day warms.</p>",
            ),
        },
    },
    # ── SNOW-254: EUREGIO/ALBINA richer per-problem fields ──────────────────
    {
        "caption": "ALBINA — wind slab with EAWS matrix + danger patterns",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=3,
                danger_level_key="considerable",
                problem_type="wind_slab",
                time_period="all_day",
                aspects=["N", "NE", "NW"],
                elevation=_ElevationBounds(
                    lower="2200",
                    upper="",
                    display="above 2200m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Wind slab",
                time_period_label="",
                core_zone_text="",
                avalanche_type="slab",
                avalanche_size=3,
                frequency_label="Some",
                stability_label="Poor",
                danger_patterns=[
                    {"label": "GM.6", "title": "Loose snow and warming"},
                    {"label": "GM.4", "title": "Cold, loose snow and wind"},
                ],
            ),
        },
    },
    {
        "caption": "SLF — persistent weak layers, no EAWS extras",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=3,
                danger_level_key="considerable",
                problem_type="persistent_weak_layers",
                time_period="all_day",
                aspects=["N", "NE", "E", "NW"],
                elevation=_ElevationBounds(
                    lower="2600",
                    upper="",
                    display="above 2600m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Persistent weak layers",
                time_period_label="",
                core_zone_text="N to E and NW aspects, above 2600m",
                comment_html=(
                    "<p>Persistent weak layers remain buried in the snowpack. "
                    "Triggering is possible even from low additional loads.</p>"
                ),
            ),
        },
    },
    # SNOW-291 — SLF split-day variants: editorial panel_title + subdivision
    {
        "caption": "SLF split-day — constant (dry, panel title, no subdivision)",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="wind_slab",
                time_period="all_day",
                aspects=["N", "NE", "NW"],
                elevation=_ElevationBounds(
                    lower="2200",
                    upper="",
                    display="above 2200m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Wind slab",
                time_period_label="",
                core_zone_text="N to NW aspects, above 2200m",
                panel_title="Dry avalanches, whole day",
                subdivision="",
                comment_html=(
                    "<p>Wind slabs on north-facing slopes remain easily triggered.</p>"
                ),
            ),
        },
    },
    {
        "caption": "SLF split-day — escalating-temporal (dry 2-, whole day)",
        "context": {
            "card": _make_rating_card(
                category="dry",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="wind_slab",
                time_period="all_day",
                aspects=["N", "NE", "E"],
                elevation=_ElevationBounds(
                    lower="2400",
                    upper="",
                    display="above 2400m",
                    bound_type=_ELEVATION_LOWER,
                ),
                label="Wind slab",
                time_period_label="",
                core_zone_text="N to E aspects, above 2400m",
                panel_title="Dry avalanches, whole day",
                subdivision="-",
                subdivision_label="lower end of the band",
                comment_html="<p>Fresh wind slabs at altitude on shaded slopes.</p>",
            ),
        },
    },
    {
        "caption": "SLF split-day — flat-temporal (wet 2, as the day progresses)",
        "context": {
            "card": _make_rating_card(
                category="wet",
                danger_level=2,
                danger_level_key="moderate",
                problem_type="wet_snow",
                time_period="later",
                aspects=["S", "SW", "SE", "W", "E"],
                elevation=_ElevationBounds(
                    lower="",
                    upper="2400",
                    display="below 2400m",
                    bound_type=_ELEVATION_UPPER,
                ),
                label="Wet snow",
                time_period_label="Afternoon",
                core_zone_text="S to W aspects, below 2400m",
                panel_title="Wet-snow avalanches, as the day progresses",
                subdivision="",
                comment_html=(
                    "<p>As temperatures rise, wet-snow slides become "
                    "more frequent on sunny slopes.</p>"
                ),
            ),
        },
    },
)


# ── Region tooltip (SNOW-201) ───────────────────────────────────────────────
# Three variants covering the two rating states (rating chip present vs absent)
# and a band of danger levels.  Fixtures use SimpleNamespace so no DB access
# is needed at library load time.

_TOOLTIP_REGION = SimpleNamespace(
    region_id="CH-VS-3431",
    name="Bex–Villars",
    subregion=SimpleNamespace(
        major=SimpleNamespace(name_en="Valais", name_native="Valais"),
        name_en="Lower Valais",
        name_native="Bas-Valais",
    ),
)
_TOOLTIP_DATE = datetime.date(2026, 2, 14)

REGION_TOOLTIP_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Considerable · no subdivision",
        "context": {
            "region": _TOOLTIP_REGION,
            "day_rating": SimpleNamespace(max_rating=3, max_subdivision=None),
            "bulletin_url": "/CH-VS-3431/",
            "country_name": "Switzerland",
            "target_date": _TOOLTIP_DATE,
        },
    },
    {
        "caption": "High · upper subdivision",
        "context": {
            "region": _TOOLTIP_REGION,
            "day_rating": SimpleNamespace(max_rating=4, max_subdivision="upper"),
            "bulletin_url": "/CH-VS-3431/",
            "country_name": "Switzerland",
            "target_date": _TOOLTIP_DATE,
        },
    },
    {
        "caption": "No bulletin",
        "context": {
            "region": _TOOLTIP_REGION,
            "day_rating": None,
            "bulletin_url": "",
            "country_name": "Switzerland",
            "target_date": _TOOLTIP_DATE,
            "covered": True,
            "provider_name": "SLF",
        },
    },
    {
        # SNOW-54: permanently-uncovered region (e.g. Swiss Lowlands / Jura).
        # The pipeline has no RegionDayRating rows for this area, so the tooltip
        # explains the upstream gap rather than showing the generic "no bulletin" label.
        "caption": "Permanently uncovered (SLF — Swiss Lowlands)",
        "context": {
            "region": _TOOLTIP_REGION,
            "day_rating": None,
            "bulletin_url": "",
            "country_name": "Switzerland",
            "target_date": _TOOLTIP_DATE,
            "covered": False,
            "provider_name": "SLF",
        },
    },
)


# ── Subscribe form (SNOW-222) ───────────────────────────────────────────────
# Four variants covering every auth/subscription state:
#   1. Anonymous — empty email-input form (original state).
#   2. Anonymous with validation error — form re-displayed with an error.
#   3. Authenticated, not yet subscribed — one-click "Add region" CTA.
#   4. Authenticated, already subscribed — one-click "Unsubscribe" CTA.
#
# Variants 3 and 4 supply a SimpleNamespace ``request.user`` so the partial's
# ``{% if not request.user.is_authenticated %}`` branch can be exercised without
# a real HTTP request.

_SUBSCRIBE_ANON_REQUEST = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))

_SUBSCRIBE_AUTHED_REQUEST = SimpleNamespace(user=SimpleNamespace(is_authenticated=True))

SUBSCRIBE_FORM_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Anonymous — empty form",
        "context": {
            "region_id": "CH-VS-3431",
            "region_name": "Bex–Villars",
            "request": _SUBSCRIBE_ANON_REQUEST,
            "user_subscribed_to_region": False,
        },
    },
    {
        "caption": "Anonymous — with validation error",
        "context": {
            "region_id": "CH-VS-3431",
            "region_name": "Bex–Villars",
            "request": _SUBSCRIBE_ANON_REQUEST,
            "user_subscribed_to_region": False,
            "form": SimpleNamespace(
                email=SimpleNamespace(
                    errors=["Enter a valid email address."],
                )
            ),
        },
    },
    {
        "caption": "Authenticated — not yet subscribed",
        "context": {
            "region_id": "CH-VS-3431",
            "region_name": "Bex–Villars",
            "request": _SUBSCRIBE_AUTHED_REQUEST,
            "user_subscribed_to_region": False,
        },
    },
    {
        "caption": "Authenticated — already subscribed",
        "context": {
            "region_id": "CH-VS-3431",
            "region_name": "Bex–Villars",
            "request": _SUBSCRIBE_AUTHED_REQUEST,
            "user_subscribed_to_region": True,
        },
    },
)


# ── Subscribe outcomes (SNOW-201) ───────────────────────────────────────────
# Five distinct outcome templates, each rendered as a separate variant under
# one sidebar entry.  The ``"partial"`` key in each variant overrides the
# category's default ``partial`` field via the widened ``include_variant``
# tag (Option A from the plan).

SUBSCRIBE_OUTCOMES_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Success — check inbox (generic)",
        "partial": "accounts/partials/subscribe_success.html",
        "context": {},
    },
    {
        "caption": "Success — access link sent",
        "partial": "accounts/partials/subscribe_success_access.html",
        "context": {},
    },
    {
        "caption": "Success — region added",
        "partial": "accounts/partials/subscribe_success_added.html",
        "context": {"region_name": "Bex–Villars"},
    },
    {
        "caption": "Success — already subscribed",
        "partial": "accounts/partials/subscribe_success_already.html",
        "context": {"region_name": "Bex–Villars"},
    },
    {
        "caption": "Error — region not found",
        "partial": "accounts/partials/subscribe_error.html",
        "context": {},
    },
)


# ── No-data-supplied (SNOW-201) ─────────────────────────────────────────────
# Single variant; the partial carries no context variables.

NO_DATA_SUPPLIED_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Default",
        "context": {},
    },
)


# Period-transition chip (SNOW-248) ----------------------------------------
# Four variants covering all three SLF patterns + the EUREGIO elevation-banded
# variant. Rendered via ``bulletin_header.html`` so the chip sits in context
# alongside the hero badge. Each variant also feeds ``day_windows.html`` so the
# transition row between the period rows is visible in the same panel.
#
# Variant shape mirrors the context that ``_bulletin_detail_response`` builds:
#   morning_rating           — hero badge (as in SNOW-246 variants)
#   period_transition_chip   — the new chip (None for flat-but-split)
#   period_transition        — PeriodTransition-like namespace for day_windows
#   day_windows              — two-row list so the transition row appears


def _make_period_transition(
    direction: str,
    destination_key: str,
    destination_number: str,
    destination_subdivision: str,
    partition_type: str,
    partition_label: str,
) -> Any:
    """Build a SimpleNamespace mimicking PeriodTransition for fixture use."""
    from types import SimpleNamespace

    return SimpleNamespace(
        direction=direction,
        destination_key=destination_key,
        destination_number=destination_number,
        destination_subdivision=destination_subdivision,
        partition_type=partition_type,
        partition_label=partition_label,
        has_split=True,
    )


def _make_transition_chip(
    level_key: str,
    chip_text: str,
    direction: str,
) -> dict[str, str]:
    """Build a period-transition chip context dict for fixture use."""
    return {
        "level_key": level_key,
        "chip_text": chip_text,
        "direction": direction,
    }


def _build_period_transition_variants() -> tuple[dict[str, Any], ...]:
    """Build the four period-transition variant fixtures (SNOW-248).

    Returns:
        Four variants:
        1. SLF escalating (all_day moderate → later considerable).
        2. SLF de-escalating (all_day considerable → later moderate).
        3. SLF flat-but-split (same level, problem type changes).
        4. EUREGIO elevation-banded (earlier lower / later upper bands).

    """
    today = datetime.date(2026, 2, 14)
    region_name = "Bex-Villars"
    subregion_name = "Vaud Alps"

    def _dw(
        period: str, level_key: str, pill_label: str, modifier: str = ""
    ) -> dict[str, Any]:
        """One day-window row dict."""
        meta = _DAY_WINDOW_LEVEL_META[level_key]
        return {
            "type": period,
            "level_key": level_key,
            "level_css": level_key.replace("_", "-"),
            "level_label": meta["label"],
            "level_number": f"{meta['number']}{modifier}",
            "caption": "",
            "pill_label": pill_label,
        }

    # Variant 1 — SLF escalating: all_day moderate, rises to L3 in the afternoon.
    slf_escalating_transition = _make_period_transition(
        direction="rise",
        destination_key="considerable",
        destination_number="3",
        destination_subdivision="",
        partition_type="temporal",
        partition_label="",
    )
    slf_escalating_chip = _make_transition_chip(
        level_key="considerable",
        chip_text="rises to L3",
        direction="rise",
    )

    # Variant 2 — SLF de-escalating: all_day considerable, falls to L2 later.
    slf_deescalating_transition = _make_period_transition(
        direction="fall",
        destination_key="moderate",
        destination_number="2",
        destination_subdivision="",
        partition_type="temporal",
        partition_label="",
    )
    slf_deescalating_chip = _make_transition_chip(
        level_key="moderate",
        chip_text="falls to L2",
        direction="fall",
    )

    # Variant 3 — SLF flat-but-split: same level, problem type changes.
    # No chip (direction="none"), but the Day Risk Profile shows a sub-caption.
    slf_flat_split_transition = _make_period_transition(
        direction="none",
        destination_key="considerable",
        destination_number="3",
        destination_subdivision="",
        partition_type="temporal",
        partition_label="",
    )

    # Variant 4 — EUREGIO elevation-banded: lower band low, upper band moderate.
    euregio_transition = _make_period_transition(
        direction="rise",
        destination_key="moderate",
        destination_number="2",
        destination_subdivision="",
        partition_type="elevation",
        partition_label="above 2600 m",
    )
    euregio_chip = _make_transition_chip(
        level_key="moderate",
        chip_text="rises above 2600 m to L2",
        direction="rise",
    )

    return (
        {
            "caption": "SLF escalating · all-day → rises to L3",
            "context": {
                "region_name": region_name,
                "subregion_name": subregion_name,
                "page_date": today,
                "morning_rating": {
                    "level_key": "moderate",
                    "level_number": "2",
                    "subdivision": "",
                },
                "period_transition_chip": slf_escalating_chip,
                "period_transition": slf_escalating_transition,
                "day_windows": [
                    _dw("all_day", "moderate", "All day"),
                    _dw("later", "considerable", "Later"),
                ],
            },
        },
        {
            "caption": "SLF de-escalating · all-day → falls to L2",
            "context": {
                "region_name": region_name,
                "subregion_name": subregion_name,
                "page_date": today,
                "morning_rating": {
                    "level_key": "considerable",
                    "level_number": "3",
                    "subdivision": "",
                },
                "period_transition_chip": slf_deescalating_chip,
                "period_transition": slf_deescalating_transition,
                "day_windows": [
                    _dw("all_day", "considerable", "All day"),
                    _dw("later", "moderate", "Later"),
                ],
            },
        },
        {
            "caption": "SLF flat-but-split · no chip — problem type changes",
            "solo": True,
            "context": {
                "region_name": region_name,
                "subregion_name": subregion_name,
                "page_date": today,
                "morning_rating": {
                    "level_key": "considerable",
                    "level_number": "3",
                    "subdivision": "",
                },
                # No chip for flat-but-split — period_transition_chip is None.
                "period_transition_chip": None,
                "period_transition": slf_flat_split_transition,
                "day_windows": [
                    _dw("all_day", "considerable", "All day"),
                    _dw("later", "considerable", "Later"),
                ],
            },
        },
        {
            "caption": "EUREGIO elevation-banded · rises above 2600 m to L2",
            "context": {
                "region_name": region_name,
                "subregion_name": subregion_name,
                "page_date": today,
                "morning_rating": {
                    "level_key": "low",
                    "level_number": "1",
                    "subdivision": "",
                },
                "period_transition_chip": euregio_chip,
                "period_transition": euregio_transition,
                "day_windows": [
                    _dw("earlier", "low", "Earlier"),
                    _dw("later", "moderate", "Later"),
                ],
            },
        },
    )


PERIOD_TRANSITION_VARIANTS: tuple[dict[str, Any], ...] = (
    _build_period_transition_variants()
)


# Season scrubber transport demo (SNOW-230). Each variant pins the static
# thumb position at a different point in the season so the library shows
# the pill at season start, mid-season, and season end. The buttons are
# not wired to JS on the library page — pressing them is a no-op.
SEASON_SCRUBBER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Season start — thumb at 0%",
        "context": {
            "today": datetime.date(2025, 12, 1),
            "today_pct": 0,
        },
    },
    {
        "caption": "Mid-season — thumb at 50%",
        "context": {
            "today": datetime.date(2026, 2, 14),
            "today_pct": 50,
        },
    },
    {
        "caption": "Season end — thumb at 100%",
        "context": {
            "today": datetime.date(2026, 4, 30),
            "today_pct": 100,
        },
    },
    {
        "caption": "Loading state",
        "context": {
            "loading": True,
            "today": datetime.date(2026, 2, 14),
            "today_pct": 50,
        },
    },
)


# ── Bulletin headline (SNOW-249) ────────────────────────────────────────────
# Four representative cells from the variant matrix — enough to review the
# copy and verify the data-testid attribute is present without wiring all 12.

BULLETIN_HEADLINE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Cell 4 — considerable, slab (most common SLF pattern)",
        "context": {
            "headline": (
                "Reactive snowpack. Considerable danger — avoid wind-loaded steeps."
            ),
        },
    },
    {
        "caption": "Cell 9 — temporal rise to considerable, wet snow",
        "context": {
            "headline": (
                "Touchy morning, dangerous afternoon"
                " — wet snow cycle developing as the day progresses."
            ),
        },
    },
    {
        "caption": "Cell 6c — high danger 4+, road-high slab",
        "context": {
            "headline": (
                "Widespread instability. High danger"
                " — road and infrastructure exposure;"
                " backcountry travel inadvisable."
            ),
        },
    },
    {
        "caption": "Generic fallback — considerable, no matrix match",
        "context": {
            "headline": ("Danger level 3 considerable. Read the bulletin carefully."),
        },
    },
)


# ALBINA band-heading rating-block variants (SNOW-292) -----------------------
# Exercises the new band_label and time_subheader fields on the card dict.
# These are set only on the first card of each ALBINA elevation band and are
# rendered by _rating_block.html above the card via data-testid="band-heading"
# and data-testid="band-time-subheader" elements.

RATING_BLOCK_ALBINA_BAND_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "ALBINA · above-2200m band heading (first card in band)",
        "context": {
            "card": {
                **_make_rating_card(
                    category="dry",
                    danger_level=3,
                    danger_level_key="considerable",
                    problem_type="persistent_weak_layers",
                    time_period="all_day",
                    aspects=["N", "NE", "E", "NW", "W"],
                    elevation=_ElevationBounds(
                        lower="2200",
                        upper="",
                        display="above 2200m",
                        bound_type=_ELEVATION_LOWER,
                    ),
                    label="Persistent weak layers",
                    time_period_label="",
                    core_zone_text="N to W aspects, above 2200m",
                    avalanche_size=3,
                    frequency_label="Some",
                    stability_label="Poor",
                ),
                "band_id": "above-2200",
                "band_label": "Above 2200 m",
            },
        },
    },
    {
        "caption": "ALBINA · below-2200m band heading (second band, first card)",
        "context": {
            "card": {
                **_make_rating_card(
                    category="dry",
                    danger_level=1,
                    danger_level_key="low",
                    problem_type="persistent_weak_layers",
                    time_period="all_day",
                    aspects=["N", "NE", "E"],
                    elevation=_ElevationBounds(
                        lower="",
                        upper="2200",
                        display="below 2200m",
                        bound_type=_ELEVATION_UPPER,
                    ),
                    label="Persistent weak layers",
                    time_period_label="",
                    core_zone_text="N to E aspects, below 2200m",
                    avalanche_size=1,
                    frequency_label="Few",
                    stability_label="Fair",
                ),
                "band_id": "below-2200",
                "band_label": "Below 2200 m",
            },
        },
    },
    {
        "caption": "ALBINA · band heading + pivot-migration subheader",
        "context": {
            "card": {
                **_make_rating_card(
                    category="wet",
                    danger_level=3,
                    danger_level_key="considerable",
                    problem_type="wet_snow",
                    time_period="earlier",
                    aspects=["E", "SE", "S", "SW", "W"],
                    elevation=_ElevationBounds(
                        lower="2500",
                        upper="",
                        display="above 2500m",
                        bound_type=_ELEVATION_LOWER,
                    ),
                    label="Wet snow",
                    time_period_label="Earlier",
                    core_zone_text="Solar aspects, above 2500m",
                    avalanche_size=3,
                    frequency_label="Some",
                    stability_label="Poor",
                ),
                "band_id": "above-2500",
                "band_label": "Above 2500 m",
                "time_subheader": "Wet line at 2500 m earlier, 2800 m later",
            },
        },
    },
)


# Favourite problem card (SNOW-422) -------------------------------------------
# Four variants — one per altitude verdict apps.favourites.relevance can produce
# (APPLIES / ABOVE / BELOW / unannotated). Reuses _make_rating_card for the
# underlying _rating_block.html shape (same helper the rating-block entry
# above uses) and layers on the altitude_relevance key
# apps.favourites.relevance.annotate_problem_relevance adds at render time — the
# only thing _favourite_problem.html itself reads before delegating to the
# shared card partial.


def _build_favourite_problem_variants() -> tuple[dict[str, Any], ...]:
    """Build one favourite-problem card fixture per altitude verdict."""
    banded_card = _make_rating_card(
        category="dry",
        danger_level=3,
        danger_level_key="considerable",
        problem_type="wind_slab",
        time_period="all_day",
        aspects=["N", "NE", "E"],
        elevation=_ElevationBounds(
            lower="2200",
            upper="",
            display="above 2200m",
            bound_type=_ELEVATION_LOWER,
        ),
        label="Wind slab",
        time_period_label="",
        core_zone_text="N to E aspects, above 2200m",
        comment_html="<p>Fresh wind slabs on N to E aspects above 2200m.</p>",
    )
    unannotated_card = _make_rating_card(
        category="wet",
        danger_level=2,
        danger_level_key="moderate",
        problem_type="wet_snow",
        time_period="all_day",
        aspects=[],
        elevation=_ElevationBounds(lower="", upper="", display="", bound_type=""),
        label="Wet snow",
        time_period_label="",
        core_zone_text="",
        comment_html=(
            "<p>Isolated wet-snow instability with no clear elevation band.</p>"
        ),
    )
    return (
        {
            "caption": "Applies at this altitude",
            "context": {"card": {**banded_card, "altitude_relevance": "APPLIES"}},
        },
        {
            "caption": "Above this location",
            "context": {"card": {**banded_card, "altitude_relevance": "ABOVE"}},
        },
        {
            "caption": "Below this location",
            "context": {"card": {**banded_card, "altitude_relevance": "BELOW"}},
        },
        {
            "caption": "Unannotated (no band / treeline)",
            "context": {"card": {**unannotated_card, "altitude_relevance": None}},
        },
    )


FAVOURITE_PROBLEM_VARIANTS: tuple[dict[str, Any], ...] = (
    _build_favourite_problem_variants()
)


# ---------------------------------------------------------------------------
# Weather panel + day picker (SNOW-761, rebuilt by SNOW-789)
# ---------------------------------------------------------------------------

# Contexts here are hand-built ``WeatherDisplay`` / ``ForecastPanel`` dicts
# rather than the output of ``build_weather_display`` against a real row: the
# component library renders without a database, and the point of the panel is
# its layout under each combination of present and absent fields. The
# derivation itself is covered in
# ``tests/weather/services/test_weather_display.py``.
#
# ``weather`` inside a WeatherDisplay is a model instance at call sites; here
# it is a plain dict, because the only thing the partial reads off it is
# ``weather_code`` for a data attribute.


def _weather_display(**overrides: Any) -> dict[str, Any]:
    """Build one WeatherDisplay-shaped context for the library.

    Args:
        **overrides: Any key to replace on the fully-populated default.

    Returns:
        The context dict.

    """
    display: dict[str, Any] = {
        "weather": {"weather_code": 73},
        "bucket": "snow",
        "is_day": True,
        "time_of_day": "day",
        "sunrise_local": "06:32",
        "sunset_local": "20:14",
        "icon_bucket": "moderate_snow",
        "condition_label": "Snow",
        "icon_filename": "moderate_snow-day.svg",
        "temp_max": -1.0,
        "temp_min": -8.0,
        "snowfall_sum": 22.0,
        "freezing_level_height": 1200.0,
    }
    display.update(overrides)
    return display


WEATHER_PANEL_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Every field present, no location label (bulletin masthead)",
        "context": {
            "weather_display": _weather_display(),
            "testid_prefix": "component-library-weather",
        },
    },
    {
        "caption": "Labelled with its location and elevation (resort page)",
        "context": {
            "weather_display": _weather_display(
                weather={"weather_code": 0},
                bucket="clear",
                icon_bucket="clear",
                condition_label="Clear",
                icon_filename="clear-day.svg",
                temp_max=6.0,
                temp_min=-2.0,
                snowfall_sum=0.0,
                freezing_level_height=2600.0,
            ),
            "location_label": "Mont Fort · 3328 m",
            "testid_prefix": "component-library-weather-labelled",
        },
    },
    {
        # Open-Meteo drops variables depending on which model backs the
        # coordinates, so a partially-populated row is ordinary. Each group
        # renders independently of the others rather than all-or-nothing.
        "caption": "Partial row — no temperatures, no freezing level",
        "context": {
            "weather_display": _weather_display(
                temp_max=None,
                temp_min=None,
                snowfall_sum=None,
                freezing_level_height=None,
            ),
            "testid_prefix": "component-library-weather-partial",
        },
    },
    {
        # Night is not a variant of the panel's chrome — it is a different
        # icon file, which is the whole visual difference.
        "caption": "Night",
        "context": {
            "weather_display": _weather_display(
                is_day=False,
                time_of_day="night",
                icon_filename="moderate_snow-night.svg",
            ),
            "testid_prefix": "component-library-weather-night",
        },
    },
)


def _forecast_panel_day(
    *,
    date: datetime.date,
    icon_bucket: str,
    condition_label: str,
    temp_max: float | None,
    temp_min: float | None,
    snowfall_sum: float | None,
    freezing_level_height: float | None,
    sunrise_local: str = "07:12",
    sunset_local: str = "17:04",
    hourly: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Build one ForecastPanelDay-shaped context column.

    Args:
        date: The column's calendar day.
        icon_bucket: The day's icon bucket.
        condition_label: The day's En-GB condition label.
        temp_max: Daily max air temperature, °C, or None.
        temp_min: Daily min air temperature, °C, or None.
        snowfall_sum: Daily snowfall total, cm, or None.
        freezing_level_height: Day's freezing level, m, or None.
        sunrise_local: Sunrise as "HH:MM" in the day's own offset.
        sunset_local: Sunset as "HH:MM" in the day's own offset.
        hourly: That day's hourly rows. Empty for a day past the horizon
            that carries no series — which is most of the week.

    Returns:
        The context dict.

    """
    return {
        "date": date,
        "weekday_label": date.strftime("%a"),
        "icon_bucket": icon_bucket,
        # Through the real helper, not an f-string that rebuilds its logic:
        # ``cloudy`` is the one bucket shipping a single file rather than a
        # day/night pair, so ``f"{bucket}-day.svg"`` asked for a
        # ``cloudy-day.svg`` that has never existed and the library rendered
        # a broken image for the Overcast column (SNOW-781).
        "icon_filename": weather_icon_filename(icon_bucket, "day"),
        "condition_label": condition_label,
        "temp_max": temp_max,
        "temp_min": temp_min,
        "snowfall_sum": snowfall_sum,
        "freezing_level_height": freezing_level_height,
        "sunrise_local": sunrise_local,
        "sunset_local": sunset_local,
        "hourly": list(hourly),
        # SNOW-789: every cell in the picker is a control, so this decides
        # only whether the day has a meteogram — presence of a series. The
        # library renders the picker and the day line separately, so
        # nothing here reveals anything; the flag is carried so the fixture
        # stays the same shape as the real ForecastPanelDay.
        "selectable": bool(hourly),
    }


def _build_weather_masthead_variants() -> tuple[dict[str, Any], ...]:
    """Build the weather-masthead variants.

    Three, because the onward links are what the variants are FOR. A
    curated point linked to a resort and standing as a region centroid
    offers both links; a bare centroid offers only the bulletin, and that
    is 461 of the estate's 540 public locations rather than an edge case;
    an anonymous point with neither is named by nothing but itself.

    Returns:
        The variant tuple.

    """
    resort = {"get_absolute_url": "/resorts/12/verbier/"}
    region = {"get_absolute_url": "/ch-4115/martigny-verbier/"}
    return (
        {
            "caption": "A curated point reaching both a resort and a region",
            "solo": True,
            "context": {
                "heading": "Mont Fort",
                "location": {"elevation_m": 3328.0},
                "resort": resort,
                "region": region,
            },
        },
        {
            "caption": "A region centroid — the bulletin is its only way on",
            "solo": True,
            "context": {
                "heading": "Val Ferret",
                "location": {"elevation_m": 2410.0},
                "resort": None,
                "region": region,
            },
        },
        {
            "caption": "No elevation, nowhere onward",
            "solo": True,
            "context": {
                "heading": "Weather station",
                "location": {"elevation_m": None},
                "resort": None,
                "region": None,
            },
        },
    )


WEATHER_MASTHEAD_VARIANTS: tuple[dict[str, Any], ...] = (
    _build_weather_masthead_variants()
)


def _build_weather_day_picker_variants() -> tuple[dict[str, Any], ...]:
    """Build the weather-day-picker variants.

    A full seven-day week, because seven is the grid the picker is built
    on (``FORECAST_DAYS``) and a shorter fixture would leave the library
    showing empty cells the real page never has. The second variant is the
    same week with the temperatures missing, which is Open-Meteo dropping
    a variable for the model backing those coordinates — the cells fall
    back to em-dashes rather than collapsing.

    Every cell is a control, so each library variant needs its own
    ``selector_name``: two pickers sharing a group name would let a click
    in one deselect the other.

    Returns:
        The variant tuple.

    """
    anchor = datetime.date(2026, 1, 12)
    conditions = (
        ("moderate_snow", "Snow", -1.0, -8.0),
        ("light_snow", "Light snow", -2.0, -9.0),
        ("cloudy", "Overcast", 0.0, -6.0),
        ("clear", "Clear", 3.0, -4.0),
        ("light_rain", "Light rain", 6.0, 1.0),
        ("thunder", "Thunderstorm", 8.0, 2.0),
        ("fog", "Fog", 4.0, -1.0),
    )
    week = [
        _forecast_panel_day(
            date=anchor + datetime.timedelta(days=index),
            icon_bucket=bucket,
            condition_label=label,
            temp_max=temp_max,
            temp_min=temp_min,
            snowfall_sum=None,
            freezing_level_height=1200.0,
            # Only the first two days carry a series — the real shape
            # (``HOURLY_DAYS`` is 2), and what decides which cells have a
            # meteogram behind them. It changes nothing here: every cell
            # is a control regardless (SNOW-789).
            hourly=({"time": "2026-01-12T09:00"},) if index < 2 else (),
        )
        for index, (bucket, label, temp_max, temp_min) in enumerate(conditions)
    ]
    return (
        {
            "caption": "A full week — the first cell opens selected",
            "solo": True,
            "context": {
                "panel": {"days": week},
                "testid_prefix": "component-library-day-picker",
                "selector_name": "component-library-day-picker",
            },
        },
        {
            "caption": "No temperatures — Open-Meteo dropped the variable",
            "solo": True,
            "context": {
                "panel": {
                    "days": [
                        {**day, "temp_max": None, "temp_min": None} for day in week
                    ]
                },
                "testid_prefix": "component-library-day-picker-sparse",
                "selector_name": "component-library-day-picker-sparse",
            },
        },
    )


WEATHER_DAY_PICKER_VARIANTS: tuple[dict[str, Any], ...] = (
    _build_weather_day_picker_variants()
)


def _build_weather_day_line_variants() -> tuple[dict[str, Any], ...]:
    """Build the weather-day-line variants.

    Neither variant passes ``reveal_index``: the library has no picker, so
    a row that opted into the hiding class would render as a blank panel.
    That is the same shape the backfilled one-day page uses.

    The second variant drops the freezing level, which Open-Meteo does
    per model. It is tested ``is not None`` rather than for truthiness on
    the page, so a genuine 0 m still prints — that is a reading, not a
    gap.

    Returns:
        The variant tuple.

    """
    anchor = datetime.date(2026, 1, 12)
    return (
        {
            "caption": "Freezing level and daylight",
            "solo": True,
            "context": {
                "day": _forecast_panel_day(
                    date=anchor,
                    icon_bucket="moderate_snow",
                    condition_label="Snow",
                    temp_max=-1.0,
                    temp_min=-8.0,
                    snowfall_sum=22.0,
                    freezing_level_height=1200.0,
                    sunrise_local="08:14",
                    sunset_local="17:02",
                ),
                "testid_prefix": "component-library-day-line",
            },
        },
        {
            "caption": "No freezing level — daylight alone",
            "solo": True,
            "context": {
                "day": _forecast_panel_day(
                    date=anchor + datetime.timedelta(days=3),
                    icon_bucket="clear",
                    condition_label="Clear",
                    temp_max=3.0,
                    temp_min=-4.0,
                    snowfall_sum=None,
                    freezing_level_height=None,
                    sunrise_local="08:11",
                    sunset_local="17:07",
                ),
                "testid_prefix": "component-library-day-line-sparse",
            },
        },
    )


WEATHER_DAY_LINE_VARIANTS: tuple[dict[str, Any], ...] = (
    _build_weather_day_line_variants()
)


# ``weather`` is a plain dict rather than a model instance: the partial
# reads one field off it, and the library renders without a database.
WEATHER_PROVENANCE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "The last of four daily fetches",
        "solo": True,
        "context": {
            "weather": {
                "fetched_at": datetime.datetime(2026, 1, 12, 18, 5, tzinfo=datetime.UTC)
            },
            "testid_prefix": "component-library-provenance",
        },
    },
)


# ── Hourly chart (SNOW-723) ──────────────────────────────────────────────

# The three real days committed under ``apps/weather/sample_days/``. Real
# observed weather rather than a generated series, because a chart fixture
# has to be shaped like weather or it cannot show whether the chart works:
# SNOW-776's meteogram fixture was an arithmetic ramp, every band rendered
# as a straight diagonal, and it was impossible to tell which of the visual
# problems were real ones. See that directory's README for what each day is
# and why these three.
_SAMPLE_DAYS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "weather" / "sample_days"
)

# Each entry is (file, caption, cursor hour). The cursor hour renders the
# time-of-day line, which only draws when the chart's day IS the current
# day — so the library pins a "now" onto each sample day rather than
# leaving every variant without the mark. Different hours across the three
# so the line is visible against different parts of the shape.
_HOURLY_CHART_CAPTIONS: tuple[tuple[str, str, tuple[int, int]], ...] = (
    (
        "2026-02-16-verbier-storm.json",
        "Storm day — 15.7 cm of snow, temperature crossing zero, gusts to 53",
        (14, 20),
    ),
    (
        "2026-01-05-verbier-cold-clear.json",
        "Cold clear day — no precipitation at all, so both bands collapse",
        (9, 45),
    ),
    (
        "2026-04-11-verbier-spring-thaw.json",
        "Spring thaw — never near freezing, so no 0 °C line and no station mark",
        (17, 0),
    ),
)


def _load_sample_day(filename: str) -> dict[str, Any]:
    """
    Read one committed sample day off disk.

    Args:
        filename: The file's name within ``apps/weather/sample_days/``.

    Returns:
        The day, in the provider's own shape.

    """
    day: dict[str, Any] = json.loads(
        (_SAMPLE_DAYS_DIR / filename).read_text(encoding="utf-8")
    )
    return day


def _build_hourly_chart_variants() -> tuple[dict[str, Any], ...]:
    """
    Build the hourly-chart variants from the three committed sample days.

    The three disagree on every axis the chart draws, which is the point:
    between them they exercise the empty bar bands, a temperature domain
    that never reaches zero, and a freezing level too far from the station
    for the elevation line to be worth drawing.

    Returns:
        The variant tuple.

    """
    variants: list[dict[str, Any]] = []
    for filename, caption, (hour, minute) in _HOURLY_CHART_CAPTIONS:
        day = _load_sample_day(filename)
        location = day["location"]
        # The sample days are local time and carry their own UTC offset, so
        # the cursor's clock is built in that offset rather than in UTC —
        # a "now" of 14:20 has to mean 14:20 at the location, not 14:20 in
        # a timezone the chart never mentions.
        at_location = datetime.timezone(
            datetime.timedelta(seconds=location["utc_offset_seconds"])
        )
        chart = build_hourly_chart(
            day,
            elevation=location["elevation"],
            location_label=location["name"],
            now=datetime.datetime.fromisoformat(day["date"]).replace(
                hour=hour, minute=minute, tzinfo=at_location
            ),
        )
        variants.append(
            {
                "caption": caption,
                "solo": True,
                "context": {
                    "chart": chart,
                    "testid_prefix": f"component-library-chart-{day['slug']}",
                    # Provenance as we can actually state it. The design
                    # handoff's copy said the freezing level is "derived
                    # using atmospheric lapse rate"; it is not — it is a
                    # variable the forecast model publishes and we request
                    # directly (see apps.weather.services.fetch).
                    "about": {
                        "elevation": f"{location['elevation']:,.0f} m",
                        "freezing": "Published by the forecast model",
                        "updated": f"{day['date']} · 06:00 {location['timezone']}",
                        "source": day["source"]["provider"],
                    },
                },
            }
        )
    return tuple(variants)


HOURLY_CHART_VARIANTS: tuple[dict[str, Any], ...] = _build_hourly_chart_variants()
