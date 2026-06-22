---
name: analytics-exempt-cacheable-api
description: Cacheable public map-data API paths are exempt from PosthogContextMiddleware to prevent Vary: Cookie from defeating CDN caching
status: current
last-reviewed: 2026-06-22
---

# Cacheable map-data API paths exempt from PosthogContextMiddleware

**Decision.** A frozenset of public, anonymous map-data endpoints
(`/api/ratings/`, `/api/regions.geojson`, etc.) is declared in
`_POSTHOG_EXEMPT_API_PATHS` in `config/settings/base.py`.
`_posthog_request_filter` returns `False` for those paths, short-circuiting
`PosthogContextMiddleware` before it can access `request.user`.

**Why.** `PosthogContextMiddleware` reads `request.user` for identity tagging
whenever `POSTHOG_API_KEY` is non-empty (i.e. in production). That access
marks the session as accessed; `SessionMiddleware.process_response` then
appends `Vary: Cookie` to the response. A `Vary: Cookie` header on a
`Cache-Control: public` endpoint tells every shared cache (CDN, proxy,
browser) to treat each cookie value as a distinct cache key — effectively
making the response uncacheable for anonymous traffic, which is 100% of
these endpoints' load.

The symptom was confirmed in SNOW-299: `test_ratings_vary_accept_encoding_not_cookie`
and `test_geojson_endpoints_have_public_cache_headers` failed when
`POSTHOG_API_KEY` was set in the environment.

**Consequences.** PostHog receives no page-view events for requests to these
paths. That is acceptable — these are anonymous JSON data endpoints, not page
views; analytics attribution of map interactions comes from the page-load
event on `/map/`, not from individual data-layer requests. The exempt set must
be kept in sync with the `@cache_control(public=True)` GET endpoints in
`public/api_urls.py`. The behaviour is regression-tested in
`tests/public/test_map_api.py` with `@override_settings(POSTHOG_API_KEY="phc_test")`.
