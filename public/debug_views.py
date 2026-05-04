"""
public/debug_views.py — staff-only design-debug pages.

These views render isolated component combinations for visual sanity checks
during design iteration. They are mounted in :mod:`public.urls` only when
``settings.DEBUG`` is True, and each view is additionally gated on a staff
``@_require_debug`` decorator so a stray production import cannot leak the
markup.

Today there is one debug page:

* :func:`header_combinations` — renders ``includes/bulletin_header.html`` once
  for every WMO weather code (day + night), in both the light and the dark
  theme, so a designer can compare icon contrast and bucket-colour shading
  against the live partial markup without having to construct 28 bulletin
  fixtures.
"""

import functools
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from bulletins.services.weather_display import (
    _ICON_BUCKET_LABEL,
    _WMO_CODE_TO_BUCKET,
    _WMO_CODE_TO_ICON_BUCKET,
    WEATHER_ICON_BUCKETS,
    WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT,
)
from pipeline.models import Region


def _require_debug(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """
    Gate a view on ``settings.DEBUG`` — production renders 404, not the page.

    Belt-and-braces with the conditional URL registration in
    :mod:`public.urls`: even if the URL pattern slipped through to a
    production deploy, this decorator prevents the view from rendering.

    Uses ``functools.wraps`` so Django's URL resolver sees the wrapped
    view's full identity (``__module__``, ``__qualname__``, ``__doc__``)
    rather than the inner ``wrapped`` closure.
    """

    @functools.wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not settings.DEBUG:
            return HttpResponseNotFound()
        return view(request, *args, **kwargs)

    return wrapped


def _synthetic_weather_display(code: int, time_of_day: str) -> dict[str, Any]:
    """
    Build a fake ``WeatherDisplay`` dict for a given WMO code and time-of-day.

    Mirrors the shape that ``build_weather_display`` returns at runtime,
    but without going through the database — the inner ``weather`` field
    is a :class:`SimpleNamespace` with just the ``weather_code`` attribute
    the partial reads.
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


@_require_debug
@staff_member_required
def header_combinations(request: HttpRequest) -> HttpResponse:
    """
    Render the bulletin header for every WMO code × time-of-day × theme.

    The 28 known WMO codes collapse to 12 distinct icon buckets — drizzle,
    light_rain, moderate_rain, etc. share a Meteocons icon and a backdrop,
    so this view groups by icon bucket and lists the WMO codes that map to
    each, rather than rendering 28 visually-identical panels.

    The page renders the same matrix twice: once in light mode and once
    inside a ``.dark`` wrapper, so the designer can sanity-check that the
    theme-invariant bucket tokens read against both page chromes.

    A no-snapshot fallback panel sits at the bottom of each theme section
    so the degraded ``data-weather-bucket="none"`` path is part of the
    review.
    """
    sections: list[dict[str, Any]] = []
    for icon_bucket in WEATHER_ICON_BUCKETS:
        codes = sorted(
            c for c, b in _WMO_CODE_TO_ICON_BUCKET.items() if b == icon_bucket
        )
        if not codes:
            continue
        sample_code = codes[0]
        sections.append(
            {
                "icon_bucket": icon_bucket,
                "label": _ICON_BUCKET_LABEL[icon_bucket],
                "codes": codes,
                "background_bucket": _WMO_CODE_TO_BUCKET.get(sample_code, "cloudy"),
                "day": _synthetic_weather_display(sample_code, "day"),
                "night": _synthetic_weather_display(sample_code, "night"),
            }
        )

    today = timezone.localdate()
    # Pick a random region so refreshing the page exercises the layout with
    # different region/sub-region text lengths — useful for catching wrap
    # issues at narrow widths that a single hardcoded fixture would hide.
    # Falls back to a known-good Vaud region if the DB has no fixtures
    # loaded (e.g. fresh dev environment before ``loaddata``).
    random_region = (
        Region.objects.select_related("subregion").order_by("?").first()  # noqa: S311 — not crypto
    )
    if random_region:
        region_id = random_region.region_id
        region_name = random_region.name
        subregion = random_region.subregion
        subregion_name = subregion.name_en or subregion.name_native
    else:
        region_id = "CH-2223"
        region_name = "Bex-Villars"
        subregion_name = "Vaud Alps"

    calendar_partial_url = reverse(
        "public:calendar_partial",
        kwargs={"region_id": region_id, "year": today.year, "month": today.month},
    )

    context = {
        "sections": sections,
        "themes": ["light", "dark"],
        "page_date": today,
        "region_name": region_name,
        "subregion_name": subregion_name,
        "calendar_partial_url": calendar_partial_url,
    }
    return render(request, "debug/bulletin_header_combinations.html", context)
