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
from types import SimpleNamespace
from typing import Any

from apps.bulletins.services.weather_display import (
    _ICON_BUCKET_LABEL,
    _WMO_CODE_TO_BUCKET,
    _WMO_CODE_TO_ICON_BUCKET,
    WEATHER_ICON_BUCKETS,
    WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT,  # used inside synthetic_weather_display
)

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


def synthetic_weather_display(
    code: int,
    time_of_day: str,
    *,
    temp_max: float | None = 4.0,
    temp_min: float | None = -2.0,
    snowfall_sum: float | None = 12.0,
) -> dict[str, Any]:
    """Build a fake ``WeatherDisplay`` dict for a given WMO code and time-of-day.

    Mirrors the shape that ``build_weather_display`` returns at runtime,
    but without going through the database — the inner ``weather`` field
    is a :class:`SimpleNamespace` with just the ``weather_code`` attribute
    the partial reads.

    ``temp_max``/``temp_min``/``snowfall_sum`` (SNOW-571) default to sample
    values so the matrix panels show the enriched meta strip out of the
    box; pass ``None`` for any of them to exercise the omit-individually
    behaviour (see ``WEATHER_PANEL_VARIANTS``'s "partially populated" entry).

    Lifted from the now-retired ``_synthetic_weather_display`` in
    ``apps/public/debug_views.py`` (which fed the ``/debug/header/`` matrix
    page from SNOW-101). The behaviour is unchanged.
    """
    bucket = _WMO_CODE_TO_BUCKET.get(code, "cloudy")
    icon_bucket = _WMO_CODE_TO_ICON_BUCKET.get(code, "cloudy")
    if icon_bucket in WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT:
        icon_filename = f"{icon_bucket}-{time_of_day}.svg"
    else:
        icon_filename = f"{icon_bucket}.svg"
    return {
        "weather": SimpleNamespace(weather_code=code),
        "bucket": bucket,
        "is_day": time_of_day == "day",
        "time_of_day": time_of_day,
        "sunrise_local": "06:13",
        "sunset_local": "20:43",
        "icon_bucket": icon_bucket,
        "condition_label": _ICON_BUCKET_LABEL[icon_bucket],
        "icon_filename": icon_filename,
        "temp_max": temp_max,
        "temp_min": temp_min,
        "snowfall_sum": snowfall_sum,
    }


def _sample_code_for_bucket(icon_bucket: str) -> int:
    """Return one representative WMO code for an icon bucket.

    Several WMO codes can map to the same bucket (e.g. drizzle covers 51,
    53, 55, 56, 57); we just need one to drive the partial. Picks the
    smallest matching code so the choice is deterministic across runs.
    """
    return min(c for c, b in _WMO_CODE_TO_ICON_BUCKET.items() if b == icon_bucket)


def _build_weather_header_variants() -> tuple[dict[str, Any], ...]:
    """Build the weather-header variant matrix.

    Two entries per icon bucket (12 buckets × day/night = 24 entries),
    plus a hero-badge level grid (all five EAWS levels × with/without
    subdivision, single panel each), plus the no-snapshot fallback at
    the end.

    Every bucket emits both day and night — even ``cloudy``, which is
    the only bucket whose *icon* is identical day vs night (it ships as
    a single ``cloudy.svg`` rather than ``cloudy-day.svg``/
    ``cloudy-night.svg``). The *background colour* still differs by
    time-of-day for cloudy (``--color-weather-cloudy-day`` vs
    ``--color-weather-cloudy-night``), so the bulletin page reads as a
    dark band on a cloudy night and a pale band on a cloudy day — the
    library mirrors that.

    Order follows ``WEATHER_ICON_BUCKETS`` so panels in the library
    appear in the same order designers see in the bucket vocabulary
    documentation. ``WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT`` is consumed
    inside ``synthetic_weather_display`` to pick the right icon
    filename — the matrix builder doesn't branch on it.
    """
    today = datetime.date(2026, 2, 14)  # mid-season, deterministic
    region_name = "Bex-Villars"
    subregion_name = "Vaud Alps"

    # Representative clear-day weather display for the badge-focused variants.
    _clear_day = synthetic_weather_display(0, "day")

    entries: list[dict[str, Any]] = []
    for icon_bucket in WEATHER_ICON_BUCKETS:
        code = _sample_code_for_bucket(icon_bucket)
        bucket_label = _ICON_BUCKET_LABEL[icon_bucket]
        for time_of_day in ("day", "night"):
            entries.append(
                {
                    "caption": f"{bucket_label} · {time_of_day}",
                    "context": {
                        "weather_display": synthetic_weather_display(code, time_of_day),
                        "region_name": region_name,
                        "subregion_name": subregion_name,
                        "page_date": today,
                        # Default to moderate (2) so the badge is always
                        # present — the bucket grid is for weather colours,
                        # not for badge level variations.
                        "morning_rating": {
                            "level_key": "moderate",
                            "level_number": "2",
                            "subdivision": "",
                        },
                    },
                }
            )

    # ---- Hero rating badge level grid (SNOW-246) -------------------------
    # Five EAWS levels × {bare number, with subdivision} = 10 panels.
    # Rendered against a fixed clear-day background so the badge fill
    # colours are the visual focus. Subdivision only at level ≥ 2 per spec.
    _badge_levels: tuple[tuple[str, str, str, str], ...] = (
        ("low", "1", "", "Level 1 — no subdivision"),
        ("moderate", "2", "", "Level 2 — no subdivision"),
        ("moderate", "2", "-", "Level 2 — minus"),
        ("moderate", "2", "+", "Level 2 — plus"),
        ("considerable", "3", "", "Level 3 — no subdivision"),
        ("considerable", "3", "-", "Level 3 — minus"),
        ("considerable", "3", "+", "Level 3 — plus"),
        ("high", "4", "", "Level 4 — no subdivision"),
        ("high", "4", "-", "Level 4 — minus"),
        ("very_high", "5", "", "Level 5 — black/red split"),
    )
    for level_key, level_number, subdivision, caption in _badge_levels:
        entries.append(
            {
                "caption": f"Hero badge · {caption}",
                "context": {
                    "weather_display": _clear_day,
                    "region_name": region_name,
                    "subregion_name": subregion_name,
                    "page_date": today,
                    "morning_rating": {
                        "level_key": level_key,
                        "level_number": level_number,
                        "subdivision": subdivision,
                    },
                },
                "solo": True,
            }
        )

    # No-snapshot fallback — the partial's degraded path. Kept last so the
    # main matrix flows top-to-bottom in canonical bucket order before the
    # edge case shows up. ``solo=True`` so it spans both columns on the
    # two-column layout (no day/night counterpart to pair it with).
    entries.append(
        {
            "caption": "No snapshot · fallback (no badge)",
            "context": {
                "weather_display": None,
                "region_name": region_name,
                "subregion_name": subregion_name,
                "page_date": today,
                # morning_rating absent — badge must not render on empty-state pages.
            },
            "solo": True,
        }
    )
    return tuple(entries)


WEATHER_HEADER_VARIANTS: tuple[dict[str, Any], ...] = _build_weather_header_variants()


def _build_weather_panel_variants() -> tuple[dict[str, Any], ...]:
    """Build the weather-panel variant matrix (SNOW-509).

    Mirrors ``_build_weather_header_variants``'s bucket × day/night grid
    plus the no-snapshot fallback, but panel-shaped: ``region_name=""``
    (no ``<h1>``) so the library shows the resort-page surface — the shape
    ``includes/_weather_panel.html`` renders for
    ``apps/public/templates/public/resort.html``, not the bulletin masthead's
    region-wayfinding variant (already covered by ``WEATHER_HEADER_VARIANTS``).
    No hero-badge grid here — ``morning_rating`` is bulletin-only context.
    """
    today = datetime.date(2026, 2, 14)  # mid-season, deterministic

    entries: list[dict[str, Any]] = []
    for icon_bucket in WEATHER_ICON_BUCKETS:
        code = _sample_code_for_bucket(icon_bucket)
        bucket_label = _ICON_BUCKET_LABEL[icon_bucket]
        for time_of_day in ("day", "night"):
            entries.append(
                {
                    "caption": f"{bucket_label} · {time_of_day}",
                    "context": {
                        "weather_display": synthetic_weather_display(code, time_of_day),
                        "region_name": "",
                        "subregion_name": "",
                        "page_date": today,
                    },
                }
            )

    # Partially-populated snapshot (SNOW-571) — temps present, snowfall NULL.
    # Demonstrates the omit-individually behaviour: the temperature group
    # renders while the snowfall group is silently absent, rather than the
    # whole meta strip falling back.
    entries.append(
        {
            "caption": "Clear · day · temps only (snowfall NULL)",
            "context": {
                "weather_display": synthetic_weather_display(
                    0, "day", snowfall_sum=None
                ),
                "region_name": "",
                "subregion_name": "",
                "page_date": today,
            },
            "solo": True,
        }
    )

    entries.append(
        {
            "caption": "No snapshot · fallback",
            "context": {
                "weather_display": None,
                "region_name": "",
                "subregion_name": "",
                "page_date": today,
            },
            "solo": True,
        }
    )
    return tuple(entries)


WEATHER_PANEL_VARIANTS: tuple[dict[str, Any], ...] = _build_weather_panel_variants()


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
        "caption": "variant=type-dry — filled amber pill for dry-avalanche problems",
        "context": {
            "text": "Dry",
            "variant": "type-dry",
            "data_testid": "category-type-pill",
        },
    },
    {
        "caption": "variant=type-wet — filled cyan pill for wet-avalanche problems",
        "context": {
            "text": "Wet",
            "variant": "type-wet",
            "data_testid": "category-type-pill",
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
            "title": "Downloads on this device",
            "close_action": "dismiss",
            "title_class": "text-lg font-bold",
        },
    },
)


# Switch (SNOW-645) -----------------------------------------------------------
# First use: the "Manage downloads" sheet's "Show areas on the map" control.
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


DAY_CHARACTER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "caption": "Hard-to-read day",
        "context": {
            "day_character": {
                "label": "Hard-to-read day",
                "explainer": (
                    "Persistent or gliding-snow problems can mask the real risk."
                ),
            }
        },
    },
    {
        "caption": "Manageable day",
        "context": {
            "day_character": {
                "label": "Manageable day",
                "explainer": "Wind slab or storm-slab problems are the main concern.",
            }
        },
    },
    {
        "caption": "Widespread danger",
        "context": {
            "day_character": {
                "label": "Widespread danger",
                "explainer": "Danger is present across the whole forecast area.",
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
    subdivision: str = "",
    avalanche_type: str | None = None,
    avalanche_size: int | None = None,
    frequency_label: str | None = None,
    stability_label: str | None = None,
    danger_patterns: list[dict[str, str]] | None = None,
    prose_mentions_spatial: bool = False,
) -> dict[str, Any]:
    """Build one rating-block card dict in the shape ``_rating_block.html`` expects."""
    return {
        "category": category,
        "danger_level": danger_level,
        "danger_level_key": danger_level_key,
        "problem_type": problem_type,
        "time_period": time_period,
        "panel_title": panel_title,
        "subdivision": subdivision,
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
    }


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
    _clear_day = synthetic_weather_display(0, "day")

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
                "weather_display": _clear_day,
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
                "weather_display": _clear_day,
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
                "weather_display": _clear_day,
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
                "weather_display": _clear_day,
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


# Forecast panel (SNOW-417, promoted to a shared partial by SNOW-572) --------
# One variant: a 3-day window mirroring build_point_forecast_panel's shape —
# two near-term days carry an hourly series (exercising the expandable
# _collapsible_panel.html detail), the third carries none (exercising the
# "no hourly" branch of the compact day strip alone).


def _build_forecast_panel_variants() -> tuple[dict[str, Any], ...]:
    """Build a representative ForecastPanel fixture for the component library."""
    days = (
        {
            "date": datetime.date(2026, 5, 1),
            "weekday_label": "Fri",
            "icon_bucket": "light_snow",
            "icon_filename": "light_snow-day.svg",
            "condition_label": "Light snow",
            "temp_max": 4.0,
            "temp_min": -3.0,
            "snowfall_sum": 12.0,
            "freezing_level_height": 1800.0,
            "hourly": [
                {
                    "time": "2026-05-01T06:00",
                    "temperature_2m": -2.0,
                    "snowfall": 0.5,
                    "precipitation": 0.5,
                    "wind_speed_10m": 10.0,
                    "wind_gusts_10m": 20.0,
                    "freezing_level_height": 1700.0,
                },
                {
                    "time": "2026-05-01T12:00",
                    "temperature_2m": 1.0,
                    "snowfall": 0.0,
                    "precipitation": 0.0,
                    "wind_speed_10m": 14.0,
                    "wind_gusts_10m": 28.0,
                    "freezing_level_height": 1800.0,
                },
            ],
        },
        {
            "date": datetime.date(2026, 5, 2),
            "weekday_label": "Sat",
            "icon_bucket": "clear",
            "icon_filename": "clear-day.svg",
            "condition_label": "Clear",
            "temp_max": 6.0,
            "temp_min": -1.0,
            "snowfall_sum": 0.0,
            "freezing_level_height": 2000.0,
            "hourly": [
                {
                    "time": "2026-05-02T06:00",
                    "temperature_2m": -1.0,
                    "snowfall": 0.0,
                    "precipitation": 0.0,
                    "wind_speed_10m": 8.0,
                    "wind_gusts_10m": 16.0,
                    "freezing_level_height": 1950.0,
                },
            ],
        },
        {
            "date": datetime.date(2026, 5, 3),
            "weekday_label": "Sun",
            "icon_bucket": "partly_cloudy",
            "icon_filename": "partly_cloudy-day.svg",
            "condition_label": "Partly cloudy",
            "temp_max": 5.0,
            "temp_min": -2.0,
            "snowfall_sum": 2.0,
            "freezing_level_height": 1900.0,
            "hourly": [],
        },
    )
    return (
        {
            "caption": "7-day strip with near-term hourly detail",
            "context": {"panel": {"days": days}},
        },
    )


FORECAST_PANEL_VARIANTS: tuple[dict[str, Any], ...] = _build_forecast_panel_variants()


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
