---
name: two-documents-and-a-map
description: Two document pages — bulletin and weather — plus the map; regions, resorts, locations, observations and routes are map objects
status: current
last-reviewed: 2026-09-02
---

# Two documents and a map

## Decision

**Snowdesk has two document pages and one application.**

- **The bulletin** — `/<region_id>/<slug>/<date>/`. The provider's CAAML
  document for one region on one day.
- **The weather page** — `/weather/<short_id>/`. One location, one day.
- **The map** — `/`. Everything else.

A thing gets a page when it is *read at length* and is *dated with an
issuer*. Nothing else qualifies. Regions, resorts, locations, observations
and routes are ways of reaching one of the two documents, and they live on
the map as layers, pins and sheets.

Two pages survive outside that rule, each for a stated reason.
`/account/settings/` holds email, passkeys and account deletion, which are
not map objects. `/resorts/<slug>/` is retained as a **landing page for a
search term** — people search "Verbier avalanche", not "CH-4115", and
`ResortSitemap` publishes every resort — but it is a router to the two
documents, not a third document.

Consequently these are removed: `/favourites/<uuid>/`, `/observations/`,
`/account/`, `/account/favourites/`, `/account/observations/`,
`/account/routes/`.

## Why

**The account list pages were already duplicates.** Favourites, routes,
observations and downloads each render from one endpoint into two surfaces —
a map sheet built on `includes/_ugc_panel.html`, and an account page. For
favourites and routes it is literally the same view, discriminated by
`?variant=map`. The map sheet is the one with the map behind it.

**Region-first was a premise the code had already half-abandoned.** The
bulletin page's tail runs adjoining regions → resorts in region → your
saved pins → subscribe form: the rings exist, ordered outside-in, with the
reader's own places ranked third of three.

**"Notifications are a feature, not the purpose."** `Subscription` was
justified as a notification channel and has never been one: no scheduled
job sends a bulletin (`schedule.py` runs `fetch_bulletins`,
`fetch_weather`, `purge_request_logs`), every sender in
`apps/accounts/services/email.py` is transactional, and the only caller of
`enqueue_push` is the staff `/account/push/test/` demo. It produces nav
links, account cards, a subscribed-state chip and an unsubscribe token for
mail that is never sent. It is a bookmark, and bookmarks belong on the map
with the other pins.

This retires the reasoning that cancelled SNOW-665 — that a subscription is
a notification channel and a favourite is a bookmark, so the two must stay
apart. The conclusion it reached (do not build one unified *list page*)
survives here for a different reason: there is no list page at all.

## Consequences

- **A region must become pinnable on the map.** This is the only genuinely
  new interaction in the change; the choropleth is a rating display with no
  pin target today. Without it `/account/` has nowhere to fold into.
- **`Favourite` becomes the one saved-place model**, absorbing what
  `Subscription` recorded. Nothing user-facing is called "Favourites" once
  the list is a map sheet whose rows are pins.
- **The weather page is promoted, and it is the weaker of the two.** It has
  full sharing metadata but no sitemap entry, and none of the MCP server's
  thirteen tools reaches it — every one is region- or bulletin-scoped. It
  gains a sitemap section and an MCP tool.
- **The bulletin page's tail collapses** to a single link back to the map,
  focused on this region.
- **The nav avatar menu drops from five destinations to two** (Settings,
  Sign out), which the account-area navigation decision already anticipated
  — see [`account-area-navigation-lives-in-the-nav-menu`](account-area-navigation-lives-in-the-nav-menu.md).
- **Every removed page keeps a redirect.** `/favourites/<uuid>/` → the
  location's weather page; the account list pages → the map with the
  matching sheet open.
- **Deleting `/observations/` costs nothing in search** — it is already
  excluded from `sitemap.xml` by design, because it shows an anonymous
  visitor a sign-in CTA rather than the stream.
- **Identifiers change with the routes** — see
  [`no-integer-pks-in-urls`](no-integer-pks-in-urls.md).
- **`docs/site-structure.md` is rewritten**, not amended: its route table is
  the map of a structure this replaces.
