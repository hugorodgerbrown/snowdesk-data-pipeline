---
name: map-page-functional-spec
description: Map page / functional spec — coverage, EAWS region layers, UGC (favourites, resorts, observations), basemaps, season scrubber
status: current
last-reviewed: 2026-07-25
---

# Map page — functional specification

This is the product-level specification for the Snowdesk map, the site's
home page (`/`). It describes what the map is for, what each layer means,
who can see and use each surface, and how the timeline works. It is
written for anyone reasoning about the map as a product — designers,
reviewers, testers, and future contributors — and complements the
implementation reference in [`map-and-api.md`](map-and-api.md), which
documents the JS, the endpoints, and the caching contract. Where the two
disagree, `map-and-api.md` is authoritative on _how_; this document is
authoritative on _what_ and _why_.

---

## 1. What the map is, and what it covers

The map is a full-screen, interactive danger-rating map of the European
Alps, rendered with MapLibre GL JS. It is the primary way a visitor
answers one question: **"what is the avalanche danger where I am going,
and how is it changing?"**

Tapping a region opens a detail surface (an anchored popup on desktop, a
docked bottom sheet during placement flows on mobile) showing that
region's danger rating for the selected day, its geographic breadcrumb,
its resorts, and a link to the full bulletin.

**Coverage.** The map covers the four avalanche services Snowdesk
ingests:

| Country | Provider | Default visibility |
|---------|----------|--------------------|
| Switzerland (CH) | SLF | **On** |
| France (FR) | Météo-France | Off (toggle on) |
| Austria (AT) | ALBINA / avalanche.report | Off (toggle on) |
| Italy — South Tyrol & Trentino (IT) | ALBINA / avalanche.report | Off (toggle on) |

Switzerland is the launch focus and the only country shown at first
paint; the other three are one toggle away in the layer menu, and their
region geometry is fetched lazily the first time a visitor turns them on
(so a CH-only visitor never pays for data they don't look at). Region
coverage is data-shaped, not prefix-based — a cross-border ALBINA
bulletin can colour a region on the "wrong" side of a national border,
and that is correct: the source of truth is which regions actually carry
a rating, not a static country list.

**The danger scale.** Every rating on the map is the EAWS 1–5 danger
scale — `1 low`, `2 moderate`, `3 considerable`, `4 high`, `5 very
high` — plus the non-numeric states `no_rating` (no bulletin published)
and `no_snow`. Colour follows the internationally standardised EAWS
palette so the map reads the same as any other European avalanche
service.

**Peak-rating rule.** Wherever the map collapses a day to a single
colour or chip — the choropleth fill, the region tooltip — it shows the
day's **peak** danger (the highest rating that applies at any time of day
or elevation band), never the morning-only or minimum value. This
matches how a travel decision should read a single daily level. The full
morning/afternoon and elevation breakdown lives on the bulletin detail
page, not on the map. See
[`compressed-views-rating-rule.md`](compressed-views-rating-rule.md).

---

## 2. What the region layers represent

Avalanche warnings are issued against a hierarchy of geographic regions
defined by the EAWS (European Avalanche Warning Services). Snowdesk
exposes three tiers of that hierarchy plus a derived "bulletin
groupings" layer. All are toggled from the **Overlays** section of the
layer menu; each remembers its state per-device in `localStorage`.

| Layer | EAWS tier | What it is | Default |
|-------|-----------|------------|---------|
| **Micro regions** | L4 | The warning region — the smallest unit a bulletin, rating, and subscription attach to (e.g. `CH-4115`). **This is the choropleth**: the coloured fill that carries the danger rating. | **On** |
| **Minor regions** | L2 | Sub-region grouping of micro-regions (e.g. `CH-41`, Lower Valais). Outline only. | Off |
| **Major regions** | L1 | Top-level area (e.g. `CH-4`, Valais). Outline only. | Off |
| **Bulletin groupings** | derived | The dissolved outer boundary of every micro-region that shares one bulletin on the selected day. Shows how the warning service actually grouped terrain that day — two regions with the same outline are covered by one text. Outline only. | Off |

The **micro-region choropleth is the map's core**: each L4 region is
filled with the colour of its peak danger rating for the selected date.
Regions with no bulletin that day are left uncoloured (`no_rating`).
Selecting a region — by tapping the fill, tapping a resort pin inside
it, or picking it from search — opens the detail surface and outlines
the region.

The L1/L2 outlines are orientation aids: they let a visitor see which
larger area a micro-region belongs to without leaving the map. The
**bulletin groupings** layer is the one derived tier — it is not a fixed
administrative boundary but a per-day artefact showing which micro-regions
the forecaster treated as one unit, and so it changes as you scrub
through the season.

(EAWS defines an L3 tier as well; Snowdesk deliberately skips it. The
"bulletin groupings" layer occupies the `l3` overlay key in code for
historical reasons but is not the EAWS L3 tier.)

---

## 3. User-generated content — favourites, resorts, observations

Three overlays carry content that originates from people rather than
from an official avalanche service. They are grouped here because they
share a property the region layers do not: **the data was created by a
human, not fetched from a forecasting authority**, and so each needs its
own answer to "who can see it, who can create it, and when."

### 3.1 Favourites — private saved pins

A **favourite** is a named point a signed-in user drops on the map to
track a specific location — a col, a slope, a hut, a chosen line — and
return to it across sessions. It is the user's own private data.

- **What it shows.** A `★` marker at the saved point, labelled with the
  user's chosen name. Tapping it opens a detail card: the point's
  coordinates and altitude, the current danger rating of the containing
  micro-region, and a 7-day point weather forecast.
- **Who can use it.** Signed-in users for whom the `favourites` feature
  flag is active (currently superusers, during rollout). Anonymous
  visitors and ineligible users never see the "Add favourite" control
  and their browsers never fetch the per-user endpoint.
- **When / how.** Eligible users get an "Add favourite" control; placing
  a pin, dragging to refine, and naming it creates the favourite (rate-
  limited to 10 creates per minute). Pins can be renamed and deleted from
  their detail sheet.
- **Default on.** Unlike the public overlays, favourites default to
  _visible_ for eligible users and are fetched at load — it is the user's
  own saved data, not a public dataset they must opt into.
- **Privacy.** Each user sees only their own favourites; the endpoint is
  owner-scoped and returns 404 (not 403) for someone else's pin, so it
  cannot be used to probe whether a pin exists.
- **Offline.** Saved favourites are cached client-side and remain visible
  offline once fetched at least once (see §6).

### 3.2 Resorts — shared reference points (treated as UGC)

A **resort** is a named ski resort geocoded onto a micro-region, letting
a visitor find a bulletin by a place they know ("Verbier") instead of an
official region id (`CH-4115`).

Resorts are treated as user-generated content deliberately: **the resort
list and its coordinates were placed by a person, offline, into a
fixture — they are not a layer any avalanche service publishes**, and the
placement of each pin may in future be crowd-sourced or corrected by
users. They are shown as reference, not as authoritative geography.

- **What it shows.** A circle pin at each geocoded resort. A resort pin is
  a _proxy for its parent region_ — tapping one selects that micro-region
  and shows its rating, exactly as tapping the region fill would.
- **Who can use it.** Everyone. The resorts overlay and the search box
  (which indexes resorts alongside regions) are public. The overlay is
  **off by default** — a visitor opts in.
- **Who can edit it.** Placement/correction of resort coordinates is a
  staff tool: the in-map resort editor at `/?edit=resorts`, gated on the
  `edit_map` flag (currently superusers). Edits land in the local
  database and are persisted back to the git fixture with a management
  command; there is no live crowd-sourced write path yet. The "may at
  some point be crowd-sourced" intent is why resorts sit in this section
  rather than under the official region layers.

### 3.3 Field observations & community reports

A **field observation** is a one-tap, GPS-gated hazard report a user
files from the map while out in the terrain — "I heard whumpfing here",
"I saw shooting cracks here". Recognised signal types are whumpfing,
pinwheels, wind striations, fractures, and shooting cracks. These are the
raw material for a crowd-sourced snowpack-signal layer.

There are two distinct surfaces over this data, always-on for eligible
users so they can be used independently:

**Submission — the "Report" flow:**

- **Who can use it.** Signed-in, email-verified users. It requires a
  location: the report captures the device GPS fix (which the user can
  refine by dragging the pin) or a manually-placed point, records how the
  location was determined, stamps the observation time, and best-effort
  resolves the point to a micro-region.
- **When.** A report can be filed offline — it is queued and replays when
  connectivity returns, stamped with the time the user actually observed
  the signal, not the time it eventually uploads.

**Read overlay — "Community reports":**

- **What it shows.** Anonymised, clustered pins of everyone's field
  observations from the **last 48 hours**, fading with age. It answers
  "what are other people seeing out there right now?"
- **Who can use it.** Everyone, including anonymous visitors — the overlay
  shows anonymised, publicly-shared data with no per-user eligibility gate.
  **Off by default** — a user opts into a shared layer rather than having
  other people's reports appear unannounced.
- **Privacy.** The feed is deliberately coarse: coordinates are rounded to
  ~100 m, timestamps are floored to the nearest 15 minutes, and no user
  identity, exact location, GPS fix, or accuracy is ever exposed.

(A third surface, the `/observations/` page — a signed-in 48-hour stream
of reports — always shows the viewer's own reports plus other users'
reports.)

### 3.4 UGC eligibility at a glance

| Surface | Visibility gate | Create/write | Default overlay state | Data class |
|---------|-----------------|--------------|-----------------------|------------|
| Favourites | Signed in | Owner only, 10/min | On (eligible users) | Private, per-user |
| Resorts | Public | Staff via `edit_map` editor | Off | Shared reference |
| Community reports | Public | via Report flow below | Off | Anonymised, public |
| Report (submit) | Signed in, verified + location | The reporter | n/a (a control, not an overlay) | Raw observation |

`edit_map` remains a superuser-scoped feature flag during rollout;
[`feature-flags.md`](feature-flags.md) is the operator reference.

---

## 4. What the basemaps represent, and why you'd choose one

The **basemap** is the geographic backdrop under all the region and UGC
layers — the terrain, roads, place names, and relief the coloured regions
sit on top of. It is purely context; it never carries a danger rating.
The basemap picker is a stacked-layers control in the top-right cluster,
and the choice persists per-device.

| Basemap | What it is | When you'd use it |
|---------|------------|-------------------|
| **Standard** | OpenFreeMap "Liberty" — a general-purpose street/terrain map covering the whole Alps. | The default. The only basemap that covers all four countries, so the sensible choice for any cross-border view. |
| **Swisstopo (CH)** | The official Swiss Federal Office of Topography winter basemap. | Detailed Swiss terrain — ski runs, contours, winter features — when you're focused on Switzerland. Covers CH only. |
| _Swisstopo light_ | A lighter swisstopo style. | Available as a deployment-level default; not surfaced as a routine picker option. |

Switching basemap re-draws the backdrop and re-installs every region and
UGC layer on top, preserving the current selection and scrubbed-to date.
The basemap origin is configurable by environment variable, so moving to
a self-hosted tile server is a config change, not a code deploy. Basemap
tiles are cached opportunistically for offline use (see §6); the danger
data never is.

---

## 5. The timeline scrubber

The scrubber is a horizontal timeline along the bottom of the map that
turns the map from "today" into "any day this season". The thumb defaults
to today's position within the **November–May** avalanche season window.

- **Scrubbing.** Dragging the thumb to a date re-colours every region
  from that day's ratings, so the visitor can see how danger evolved
  across the season — a cold snap, a storm cycle, a thaw. The open detail
  surface follows the scrubber, re-fetching the bulletin for the
  scrubbed-to date so the text always matches the colour.
- **Timelapse.** A play button steps through the season automatically,
  animating the choropleth day by day as a time-lapse of the winter.
- **Derived layers follow.** The bulletin-groupings layer re-dissolves for
  each scrubbed date (it only redraws once the scrubber settles, so it
  never thrashes during a drag or playback).
- **Performance.** The full season's ratings are fetched once, on the
  first scrub, and served from memory thereafter — the first scrub pays a
  single round-trip; every later scrub and all playback render instantly.
  The cold map load only fetches today's ratings, keeping first paint
  small.
- **Shareable.** The scrubbed date is reflected in the URL (`/?d=YYYY-MM-DD`),
  so a specific day's map can be linked and reloaded.

---

## 6. Other behaviours

**Search.** The header hosts a client-side autocomplete over the regions
and resorts already loaded, so there are no extra round-trips. Matching
is accent-insensitive, prefix matches rank above substring matches, and
each result carries a "Region" or "Resort" badge to disambiguate places
whose region and resort share a name (e.g. Davos). Selecting a result
routes through the same selection path as a map tap.

**Marker tap priority.** Favourite, community-report, and cluster markers
sit above the choropleth. A tap resolves to exactly one target in this
order: **overlay controls → markers → resort pin → region fill → empty-
area deselect.** Tapping a favourite or community pin activates that
marker and never also selects the region underneath it. Resort pins are
the deliberate exception — because a resort is a proxy for its region,
tapping one still selects that region.

**Offline / PWA.** The map is a Progressive Web App and is installable to
a home screen. The service worker caches the app shell and the region
geometry so a second visit paints instantly, and caches basemap tiles for
areas the user has actually browsed. Safety-critical data (danger
ratings, bulletin text) is **never** served stale from cache — it is
always fetched fresh or shown as explicitly expired, on the principle
that a stale avalanche rating is worse than no rating. Two offline aids
close the gaps this leaves:

- Favourites and community-reports overlays get a client-side write-
  through cache so they still render offline once fetched (favourites
  never expire on read-back; community reports re-apply their 48-hour age
  filter). When an overlay can't load and nothing is cached, a small
  "unavailable offline" toast explains why rather than the toggle looking
  broken.
- A one-shot **"Cache this area for offline"** command warms the current
  viewport's basemap tiles and the region data feeds for the area on
  screen. It is an explicit, bounded action — not a background download.

When the basemap style itself can't be fetched offline, the map falls
back to a plain background so the SW-cached region colours still paint
instead of showing a blank canvas.

**Layers as an interaction option.** Beyond visual overlays, the layer
menu's **Options** section carries interaction toggles (e.g. auto-zoom on
selection) and the "Cache this area" command. These change behaviour, not
what is drawn.

**Edit-resorts mode.** `/?edit=resorts`, gated on the `edit_map` flag, is
the staff tool for placing and correcting resort coordinates (§3.2). The
URL is safe to bookmark: without the flag it silently renders the normal
map.

---

## Related documents

- [`map-and-api.md`](map-and-api.md) — implementation reference: the map
  JS, the `/api/` endpoints, the caching and payload contracts.
- [`offline-map.md`](offline-map.md) — PWA shell, service worker, cache
  strategy, "Cache this area", kill switch.
- [`offline-first.md`](offline-first.md) — offline-first compliance index
  (version/freshness headers, mutation queue, reset).
- [`compressed-views-rating-rule.md`](compressed-views-rating-rule.md) —
  the peak-rating rule the choropleth and tooltip follow.
- [`feature-flags.md`](feature-flags.md) — the flag gating the resort
  editor (`edit_map`).
- [`glossary.md`](glossary.md) — domain term → code symbol map (EAWS
  tiers, MicroRegion, FieldObservation, Resort, RegionDayRating).
</content>
</invoke>
