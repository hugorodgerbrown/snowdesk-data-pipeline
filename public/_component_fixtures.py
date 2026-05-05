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
    calendar_partial_url = "#"  # Library never round-trips through HTMX.

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
                        "calendar_partial_url": calendar_partial_url,
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
                "calendar_partial_url": calendar_partial_url,
            },
            "solo": True,
        }
    )
    return tuple(entries)


WEATHER_HEADER_VARIANTS: tuple[dict[str, Any], ...] = _build_weather_header_variants()


def _build_masthead_variants() -> tuple[dict[str, Any], ...]:
    """Build the bulletin-masthead variant matrix.

    Four hand-curated variants, rendered as a vertical stack
    (``panel_layout="stack"``):

    1. Default — short region name, sub-region present, weather icon
       (clear-day) present, calendar trigger present.
    2. No sub-region — exercises the ``{% if subregion_name %}`` guard
       that hides the H2 for regions without an EAWS L2 parent.
    3. No weather snapshot — ``weather_display=None`` so the icon is
       omitted; the map-pin link slides up to fill the gap.
    4. Long region name — exercises H1 wrap behaviour and the
       ``bm-region-row`` flex reflow at narrow widths.

    Date and region_id are deterministic so dev-preview screenshots are
    reproducible across machines. The calendar URL is ``"#"`` — the
    library never round-trips through HTMX, so a placeholder href keeps
    the trigger button rendered without firing a real request.
    """
    today = datetime.date(2026, 2, 14)  # mid-season, deterministic
    region_id = "7551"  # Representative SLF L4 region id; not the focus.
    calendar_partial_url = "#"  # Library never round-trips through HTMX.
    weather = synthetic_weather_display(0, "day")  # WMO 0 = clear sky.

    return (
        {
            "caption": "Default · region + subregion + weather",
            "context": {
                "page_date": today,
                "region_name": "Bex-Villars",
                "region_id": region_id,
                "subregion_name": "Vaud Alps",
                "calendar_partial_url": calendar_partial_url,
                "weather_display": weather,
            },
        },
        {
            "caption": "No sub-region",
            "context": {
                "page_date": today,
                "region_name": "Bex-Villars",
                "region_id": region_id,
                "subregion_name": "",
                "calendar_partial_url": calendar_partial_url,
                "weather_display": weather,
            },
        },
        {
            "caption": "No weather snapshot",
            "context": {
                "page_date": today,
                "region_name": "Bex-Villars",
                "region_id": region_id,
                "subregion_name": "Vaud Alps",
                "calendar_partial_url": calendar_partial_url,
                "weather_display": None,
            },
        },
        {
            "caption": "Long region name · wrap test",
            "context": {
                "page_date": today,
                "region_name": "Vorderrhein und Hinterrhein bis Andermatt",
                "region_id": region_id,
                "subregion_name": "Glarner Alpen und nördliches Bündnerland",
                "calendar_partial_url": calendar_partial_url,
                "weather_display": weather,
            },
        },
    )


MASTHEAD_VARIANTS: tuple[dict[str, Any], ...] = _build_masthead_variants()
