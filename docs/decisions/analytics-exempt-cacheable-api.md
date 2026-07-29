---
name: analytics-exempt-cacheable-api
description: Cacheable public endpoints are exempt from PosthogContextMiddleware to prevent Vary: Cookie from defeating CDN caching
status: current
last-reviewed: 2026-06-22
---

# Cacheable public endpoints exempt from PosthogContextMiddleware

**Decision.** A frozenset of public, anonymous endpoints is declared in
`_POSTHOG_EXEMPT_PATHS` in `config/settings/base.py`.
`_posthog_request_filter` returns `False` for those paths, short-circuiting
`PosthogContextMiddleware` before it can access `request.user`.

The set covers two categories:

- **Map-data JSON/GeoJSON API endpoints** (`/api/ratings/`, `/api/regions.geojson`,
  etc.) — added in SNOW-299.
- **Static public-good documents** (`/robots.txt`, `/llms.txt`,
  `/manifest.webmanifest`, `/favicon.ico`, `/favicon.ico/`) — added in
  SNOW-338. All set `Cache-Control: public` and read no per-user state, so
  the exemption fully removes `Vary: Cookie` from their responses.

**`/sitemap.xml` is deliberately excluded.** During SNOW-338 it was verified
empirically that `/sitemap.xml` (a) sets **no** `Cache-Control` header at all —
so it is not a shared-cacheable surface — and (b) carries `Vary: Cookie` from
the sitemap-view middleware path **regardless of PostHog**: the header persists
with `POSTHOG_API_KEY` unset, and the sitemap view itself does not access the
session. Exempting it from `PosthogContextMiddleware` would therefore be dead
config — it would neither strip `Vary: Cookie` nor make the response cacheable.
Making the sitemap a genuine public-cacheable surface (set `Cache-Control:
public` and eliminate the non-PostHog `Vary: Cookie` source) is tracked in
SNOW-340.

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

**Why a request-phase path filter rather than a response-phase predicate.**
The session is accessed at *request* time — when `PosthogContextMiddleware`
reads `request.user` — so `Vary: Cookie` is already baked into the response
before any response-phase middleware can inspect `Cache-Control: public`. A
response-phase predicate keyed on `Cache-Control: public` would therefore see
the header too late to prevent the corruption. Stripping `Vary: Cookie` in a
post-hoc middleware would be fragile (it could mask legitimate variation on
other public pages). The only correct fix is a *request-phase* path filter,
which is what `_posthog_request_filter` implements.

**Consequences.** PostHog receives no page-view events for requests to these
paths. That is acceptable — these are anonymous data or infrastructure
endpoints, not page views; analytics attribution of map interactions comes from
the page-load event on `/map/`, not from individual data-layer requests. The
exempt set must be kept in sync with the `@cache_control(public=True)` GET
endpoints in `apps/public/api_urls.py` and the `Cache-Control: public` static routes
in `config/urls.py`. The behaviour is regression-tested with
`@override_settings(POSTHOG_API_KEY="phc_test")` in
`tests/public/test_map_api.py` (API endpoints) and in the per-surface test
files for each static route (SNOW-338). `tests/public/test_sitemap.py`
additionally guards the deliberate *non*-exemption of `/sitemap.xml`.
