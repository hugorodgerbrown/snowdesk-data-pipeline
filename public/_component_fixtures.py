"""
public/_component_fixtures.py — Synthetic context for the component library.

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

import datetime
from types import SimpleNamespace
from typing import Any

from bulletins.services.weather_display import (
    _ICON_BUCKET_LABEL,
    _WMO_CODE_TO_BUCKET,
    _WMO_CODE_TO_ICON_BUCKET,
    WEATHER_ICON_BUCKETS,
    WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT,  # used inside synthetic_weather_display
)


def synthetic_weather_display(code: int, time_of_day: str) -> dict[str, Any]:
    """Build a fake ``WeatherDisplay`` dict for a given WMO code and time-of-day.

    Mirrors the shape that ``build_weather_display`` returns at runtime,
    but without going through the database — the inner ``weather`` field
    is a :class:`SimpleNamespace` with just the ``weather_code`` attribute
    the partial reads.

    Lifted from the now-retired ``_synthetic_weather_display`` in
    ``public/debug_views.py`` (which fed the ``/debug/header/`` matrix
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
    plus the no-snapshot fallback at the end. Every bucket emits both
    day and night — even ``cloudy``, which is the only bucket whose
    *icon* is identical day vs night (it ships as a single ``cloudy.svg``
    rather than ``cloudy-day.svg``/``cloudy-night.svg``). The
    *background colour* still differs by time-of-day for cloudy
    (``--color-weather-cloudy-day`` vs ``--color-weather-cloudy-night``),
    so the bulletin page reads as a dark band on a cloudy night and a
    pale band on a cloudy day — the library mirrors that.

    Order follows ``WEATHER_ICON_BUCKETS`` so panels in the library
    appear in the same order designers see in the bucket vocabulary
    documentation. ``WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT`` is consumed
    inside ``synthetic_weather_display`` to pick the right icon
    filename — the matrix builder doesn't branch on it.
    """
    today = datetime.date(2026, 2, 14)  # mid-season, deterministic
    region_name = "Bex-Villars"
    subregion_name = "Vaud Alps"

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
                    },
                }
            )

    # No-snapshot fallback — the partial's degraded path. Kept last so the
    # main matrix flows top-to-bottom in canonical bucket order before the
    # edge case shows up. ``solo=True`` so it spans both columns on the
    # two-column layout (no day/night counterpart to pair it with).
    entries.append(
        {
            "caption": "No snapshot · fallback",
            "context": {
                "weather_display": None,
                "region_name": region_name,
                "subregion_name": subregion_name,
                "page_date": today,
            },
            "solo": True,
        }
    )
    return tuple(entries)


WEATHER_HEADER_VARIANTS: tuple[dict[str, Any], ...] = _build_weather_header_variants()


# Day-windows panel (SNOW-107) ---------------------------------------------
# Mirrors the dict shape produced by ``_build_day_windows`` in
# ``public/views.py``. Labels and numbers below mirror ``_DANGER_PANEL_META``
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
    period: str, level_key: str, pill_label: str, modifier: str = ""
) -> dict[str, Any]:
    """Build one day-window row dict for the component-library fixture."""
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


def _build_day_windows_variants() -> tuple[dict[str, Any], ...]:
    """Build the day-windows variant matrix.

    Four stacked variants:

    * **All-day, level grid** — five synthetic ``all_day`` rows stepping
      ``low → very_high`` so tile + label contrast is reviewable across
      the whole EAWS scale on one screen. Not a realistic bulletin
      (real bulletins have at most two windows) — a comparison harness.
    * **All-day with sublevel modifier** — one ``all_day`` row at
      considerable with a ``−`` modifier (badge reads ``3−``).
    * **Cross-category later** — ``all_day`` low + ``later`` moderate,
      the most common two-row shape in the bulletin sample.
    * **Within-category later** — ``all_day`` considerable−  + ``later``
      considerable (badge differential shows the intra-band rise).
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
    )


DAY_WINDOWS_VARIANTS: tuple[dict[str, Any], ...] = _build_day_windows_variants()


def _build_season_calendar_variants() -> tuple[dict[str, Any], ...]:
    """Build the season calendar demo variant for the component library.

    Constructs a synthetic 13-week SeasonGrid (Nov 2025 – Jan 2026) with
    hand-picked cells covering every cell state: no-rating, all five EAWS
    solid levels, split pairs (afternoon-elevated), today, and selected.
    No database access — purely synthetic fixture data.
    """
    from public.season_calendar import SeasonCell, SeasonGrid

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

    # 13 weeks × 7 days.  Each entry is a short-code string (solid cell) or
    # a (min, max) tuple (split / afternoon-elevated cell).
    _schedule: list[str | tuple[str, str]] = [
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
        # Week 7  Dec 15–21  split cells (afternoon-elevated days)
        ("L", "M"),
        ("L", "C"),
        ("M", "C"),
        ("M", "H"),
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

        if isinstance(entry, tuple):
            min_key = _KEY[entry[0]]
            max_key = _KEY[entry[1]]
        else:
            min_key = max_key = _KEY[entry]

        has_bulletin = min_key != "no_rating"
        cells.append(
            SeasonCell(
                date=d,
                min_rating_key=min_key,
                max_rating_key=max_key,
                subdivision="dry",
                has_bulletin=has_bulletin,
                is_today=d == _today,
                is_selected=d == _selected and d != _today,
                month_parity=month_parity,
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
    return (
        {
            "caption": "Full season — all cell states",
            "context": {"season_calendar": grid},
        },
    )


SEASON_CALENDAR_VARIANTS: tuple[dict[str, Any], ...] = _build_season_calendar_variants()


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
