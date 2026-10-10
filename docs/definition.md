---
name: definition
description: What Snowdesk is — core mission (fidelity, reach, availability, alerting) and the full verified feature inventory
status: draft
last-reviewed: 2026-09-13
---

# Snowdesk

**What it is.** A free, browser-based avalanche bulletin reader for the Alps.
It fetches the official bulletins from SLF (Switzerland), Météo-France and
ALBINA (Austria / South Tyrol / Trentino), normalises them to CAAML v6, and
renders them on one map and one page shape — so a trip that crosses a border
is still one app.

**Core mission.** The bulletin is the authority; Snowdesk is a way of reading
it, not a substitute for it. Three commitments follow:

- **Fidelity** — show the provider's bulletin in full rather than a
  simplified subset
  ([why](decisions/bulletin-fidelity-over-simplification.md), enforced by
  `fidelity-lint`).
- **Reach** — several countries in one place, free, no ads, nothing to
  install, nothing to buy.
- **Availability** — it has to work in a valley with no signal, which is why
  the whole thing is an offline-first PWA rather than a website with a cache
  ([`docs/offline-first.md`](offline-first.md)).
- **Reaching you** — a reader who does not check on Thursday and skis on
  Friday is a failure of the mission, not a user error. Alerting is in
  scope and unbuilt; see [`roadmap.md`](roadmap.md). Settled 2026-09-13.

## Features

- **Bulletins** — the full provider bulletin per region and day, with danger
  ratings, avalanche problems, aspect/elevation geometry and provider prose;
  EAWS glossary terms marked up inline; past days immutable; a day-character
  summary and a calendar of longitudinal ratings per region.
- **The map** (`/`) — the homepage, not a landing page. Danger choropleth by
  region and date, a date scrubber, national topographic basemaps (CH, FR,
  AT) plus OpenFreeMap, a slope-angle layer, weather and community layers,
  and sheets for pins, routes and reports.
- **Weather** — Open-Meteo forecasts as a second document type: one immutable
  row per location per day, a seven-day picker, house-drawn icons, plus
  historical backfill.
- **Saved places** — favourites as map pins or region pins, resorts as search
  landing pages routing to the bulletin and to each curated location's
  weather page.
- **Routes and trips** — upload your own GPX; turn a route into a shareable
  trip with a day, meeting time and point, sent as one link that anyone can
  save.
- **Community reports** — field observations submitted from the map, with
  owner-only precision on the reporter's own address.
- **Offline** — a service worker plus downloadable basemap areas pinned in
  the device's own cache, verified by what they render rather than by tile
  count; an explicit offline mode, an offline-content audit report, and a
  weekly real-tile assurance suite.
- **Accounts** — signed-token sign-in, passkeys, one settings page, and web
  push *plumbing* — registration, a test send, and 404/410 reconciliation.
  Nothing in the codebase triggers a push on rising danger or a bulletin
  re-issue; `enqueue_push` has exactly one caller, `push_views.push_test`.
- **Also** — a JSON API, a JSON-RPC MCP server for agents, and PostHog
  telemetry.

## Honest gaps

No alerting (see [`roadmap.md`](roadmap.md)), no live weather stations
(forecasts only), no per-tour risk scoring, and
slope-angle coverage is incomplete across the Alps. [`/compare/`](../apps/public/competitor_matrix.py)
says so on the public site, alongside the cases where a competitor is the
better choice.
