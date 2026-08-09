---
name: icon-ch-domain-bounding-box
description: ICON-CH point-forecast model selection is gated by a loose lat/lon bounding box, not a polygon, with a day-0 fallback to the default chain
status: current
last-reviewed: 2026-08-09
---

# ICON-CH selection is gated by a deliberately loose bounding box

**Decision.** `fetch_weather_for_point` requests
`models=meteoswiss_icon_ch2` when a point falls inside
`ICON_CH_BOUNDS = (44.5, 48.5, 5.0, 11.5)` — a plain lat/lon rectangle, not a
geometric approximation of the model's real domain. Points outside send no
`models=` and keep Open-Meteo's default blended chain. A 400, or a day 0 whose
`weather_code` / `sunrise` / `sunset` is null, falls back to the default chain
once and persists that instead.

**Why.** ICON-CH returns a 400 for a location outside its domain rather than
degrading gracefully, so the gate has to be applied before the request. There
was no domain-gating helper to reuse: the `bbox` fields on `MicroRegion` and
`MajorRegion` are per-region GeoJSON bounds from `refresh_eaws_fixtures`, not
an "is this Alpine" predicate.

The box is wider than Switzerland on purpose. ICON-CH's real domain
comfortably covers Milan and Chamonix, and Snowdesk serves the wider Alpine
arc anyway — ALBINA for AT-07 and IT-32-BZ/TN, Météo-France for the French
Alps. A box drawn tight to the Swiss border would silently deny those regions
the better model, which is the failure that would go unnoticed; the reverse
failure costs one wasted HTTP request per miss and is corrected by the
fallback.

Precision in the gate would be wasted effort for the same reason: the fallback
already makes a wrong answer cheap, so a polygon would buy accuracy nobody
can observe at the cost of geometry nobody can maintain.

**Consequences.** The fallback is not defensive padding.
`ForecastPointWeather.weather_code` is a `PositiveSmallIntegerField` with no
`null=True`, and `_build_point_defaults` indexes `weather_code`, `sunrise` and
`sunset` directly rather than via `.get()` — so a partial ICON-CH payload
raises at `update_or_create` instead of skipping the row. Removing the
fallback would turn every in-domain point with no near-term ICON-CH skill into
a failed point.

Only day 0 is checked. ICON-CH2 runs about five days into the seven-day
window, so days 5–6 legitimately come back null and are handled by the
existing degrade-to-`None` pattern. Falling back for those would throw away
four high-resolution days to rescue two the default chain barely resolves
either.

Only a **400** triggers the fallback. Any other status — a rate limit, an
outage — propagates, so `fetch_all_points` still counts the point as failed
rather than hiding a real problem behind a second request.

The gate applies to the point-forecast path only. `fetch_weather_for_region`
still uses the default chain; region snapshots feed the bulletin header, which
does not carry the per-point detail the higher resolution buys.
