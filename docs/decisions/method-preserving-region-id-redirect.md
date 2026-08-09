---
name: method-preserving-region-id-redirect
description: POST views behind lowercase_region_id redirect with 308 not 301, so an htmx POST to an uppercase EAWS id is not downgraded to GET and 405ed
status: current
last-reviewed: 2026-08-09
---

# Method-preserving redirect for region-scoped POST endpoints

**Decision.** `lowercase_region_id` (`apps/public/decorators.py`) canonicalises
a mixed-case `region_id` with a **301** on full-page GET views, and with a
**308** — via `preserve_method=True` — on every method-restricted endpoint.
The four POST-only call sites (`public.fetch_weather_snippet`,
`accounts.add_region`, `accounts.remove_region_from_bulletin`,
`accounts.remove_region`) opt in; the GET-only ones do not.

**Why.** Every EAWS region id is uppercase (`CH-4115`) and the templates build
their `hx-post` from the raw model field, so the redirect fires on essentially
every click of a region-scoped control. A user agent following a **301** on a
POST re-issues it as a **GET** — standard behaviour, not an htmx quirk — which
then meets `@require_POST` and gets a 405. The swap never happens. That made
the weather retry permanently blank and the manage page's "Remove" button inert
for every user on every click, invisibly, because every view test posted
straight to the already-lowercase URL (SNOW-650).

The alternatives were each worse in a specific way:

- **Lowercase the id in each template.** Whack-a-mole on a shared root cause;
  it had already missed three call sites by the time the bug was diagnosed, and
  it re-breaks the moment a new template posts to `region.region_id`.
- **Accept GET on the affected views.** Lets `add_region` / `remove_region`
  mutate from a plain GET, which is wrong for endpoints exposed to prefetchers
  and link-checkers.
- **Exempt the partial routes from the decorator.** Loses canonicalisation
  entirely, so one logical endpoint fragments across cache keys, log lines and
  analytics paths by casing.

308 is the narrow fix: it keeps the canonicalisation and changes only the
method loss.

**Consequences.** Any new region-scoped endpoint that restricts its method
must pass `preserve_method=True` — the bare `@lowercase_region_id` is correct
only for full-page GET views, where 301 is also the SEO signal to search
engines. Django ships no 308 response class, so the decorator module owns
`HttpResponsePermanentRedirectPreserveMethod`; keep it there rather than
reaching for a raw `HttpResponse(status=308)` at a call site.

A related trap shares the same blast radius and is guarded separately:
`public/base.html` does not load htmx globally, so a page emitting `hx-*`
attributes without self-loading the script is equally inert.
`tests/public/test_htmx_pages.py` walks every public page and fails that case.
