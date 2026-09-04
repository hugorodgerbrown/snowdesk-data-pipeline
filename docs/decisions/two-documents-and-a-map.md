---
name: two-documents-and-a-map
description: Two document pages — bulletin and weather — plus the map; regions, resorts, observations and routes are map objects; a trip is not
status: current
last-reviewed: 2026-09-04
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

Three pages survive outside that rule, each for a stated reason.
`/account/settings/` holds email, passkeys and account deletion, which are
not map objects. `/resorts/<slug>/` is retained as a **landing page for a
search term** — people search "Verbier avalanche", not "CH-4115", and
`ResortSitemap` publishes every resort — but it is a router to the two
documents, not a third document. **`/trips/<uuid>/` is a page because a trip
is authored to be sent** (SNOW-819): it is one person's plan for one day,
addressed to other people, and a link to it has to unfurl as a card in a
message and open as something a recipient can read before being asked for
anything. A redirect into the map would show them a track and nothing else —
not the day, not the meeting time, not the note. It is also the first
Snowdesk object that is **interactive and multi-user**: everything the map
holds is one account's own data drawn on shared reference data, whereas a
trip is authored by one account and read, then joined, by others.

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
survives here for a different reason: no SAVED-THING list page is a page.

**Amended by SNOW-823: `/trips/` is a list page, and the rule that admits
it is about the INDEX.** A route, a favourite and an observation are indexed
*spatially* — where they are is what you look them up by — and the map IS
that index, which is exactly why SNOW-803 could delete their list pages
without losing anything. A trip is indexed *temporally*: what you want to
know is what is coming up, and in what order.

The map has no index for that. Its scrubber picks a date to read the
BULLETIN for; it does not filter the reader's own objects by date, and
teaching it to would make one control mean two things. Nor could it: a trip
dated three weeks out is not a thing you find by looking at a place.

Put the other way round: **a collection of routes is an inventory, and a
collection of trips is an agenda.** An inventory is browsed by attribute —
a map is a very good attribute browser. An agenda is read in order, forwards
from today, and that is a list.

So the sentence above is narrowed, not reversed. The map is still the index
for everything indexed by place; `/trips/` exists because a trip is not.

## Consequences

- **A region must become pinnable on the map.** This is the only genuinely
  new interaction in the change; the choropleth is a rating display with no
  pin target today. Without it `/account/` has nowhere to fold into.
- **`Favourite` becomes the one saved-place model**, absorbing what
  `Subscription` recorded. A region pin is a `Favourite` with `region` set
  and no coordinate, elevation or `Location` — it is deliberately excluded
  from `favourites.geojson`, because it has nothing to place (SNOW-802).
  Nothing user-facing is called "Favourites" once the list is a map sheet
  whose rows are pins.

  It lived in the pins sheet for one ticket, and that was the wrong home
  (SNOW-814). "One model" was read as "one list", but the sheet's whole
  vocabulary is a place's: a coordinate for the map layer and its "Display
  on the map" switch, a name of the user's own to rename, a forecast card to
  expand. A region has none of those, so its row was a hole in every column,
  and the pin control that creates one was on a different surface entirely.
  Pinned regions now list in the region + date panel the readout chip opens,
  and pinning is a star roundel in that chip's own header beside the
  download roundel — the region's two verbs together, on the surface that
  already names it — while the sheet is places again. One model, two lists, and the second one is
  what makes the chip mean something with nothing selected.
- **The weather page is promoted, and it is the weaker of the two.** It had
  full sharing metadata but no sitemap entry, and none of the MCP server's
  thirteen tools reached it — every one was region- or bulletin-scoped. It
  gained a sitemap section, a canonical URL and two MCP tools (SNOW-799).
- **The bulletin page's tail collapses** to a single link back to the map,
  focused on this region.
- **The nav avatar menu drops from five destinations to two** (Settings,
  Sign out), which the account-area navigation decision already anticipated
  — see [`account-area-navigation-lives-in-the-nav-menu`](account-area-navigation-lives-in-the-nav-menu.md).
  SNOW-823 adds a third, Trips. That is not a reversal of SNOW-803's
  removal: what came out were three lists of MAP OBJECTS, and this is the
  time-indexed exception the amendment above establishes. The menu's own
  rule already said adding a destination is adding one `<a>`.
- **A trip is the first INTERACTIVE, MULTI-USER object.** Everything else
  the map holds is one account's own data drawn on shared reference data —
  authored by one person, read by that person. A trip is authored by one
  account and read, then joined, by others, which is a new category. It is
  written down here and in
  [`a-trip-is-one-object-with-a-roster`](a-trip-is-one-object-with-a-roster.md)
  so the next such object does not have to re-argue it.
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
