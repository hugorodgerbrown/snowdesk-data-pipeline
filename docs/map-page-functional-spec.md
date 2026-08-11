---
name: map-page-functional-spec
description: Map page / functional spec — coverage, EAWS region layers, UGC (favourites, resorts, observations), weather overlay, basemaps
status: current
last-reviewed: 2026-08-08
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

Tapping a region selects it: the region is outlined, and the season
ribbon and its readout chip at the top of the map switch to that
region — its name as a geographic breadcrumb, its danger swatch for the
selected day, and the arrow roundel linking to the full bulletin. The
selection is mirrored in the URL fragment (`#CH-4115`), so it is
deep-linkable and the back button drops it. Nothing is overlaid on the
map itself: tapping a region does not open a popup over the terrain the
visitor just tapped.

Tapping the selected region again deselects it, as does tapping empty
map area outside any region; the ribbon greys out and the readout drops
back to the date alone.

**Coverage.** The map covers the four avalanche services Snowdesk
ingests:

| Country | Provider | Default visibility |
|---------|----------|--------------------|
| Switzerland (CH) | SLF | **On** |
| France (FR) | Météo-France | Off (toggle on) |
| Austria (AT) | ALBINA / avalanche.report | Off (toggle on) |
| Italy — South Tyrol & Trentino (IT) | ALBINA / avalanche.report | Off (toggle on) |

Switzerland is the launch focus and the only country shown at first
paint; the other three are one toggle away in the layer menu — where the
rows are **one per provider**, so Austria and Italy share a single "ALBINA
(AT, IT)" row (SNOW-658): a row switches one warning service's bulletins on,
and ALBINA issues one EUREGIO bulletin covering both. Tapping it turns both
countries on together, and its offline-status dot goes green only once both
countries' data is cached. Their
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
groupings" layer. All are toggled from the **Boundaries** section of the
layer menu; each remembers its state per-device in `localStorage`.

| Layer | EAWS tier | What it is | Default |
|-------|-----------|------------|---------|
| **Micro regions** | L4 | The warning region — the smallest unit a bulletin, rating, and subscription attach to (e.g. `CH-4115`). Its boundary and its label. | **On** |
| **Bulletin fill** | derived | The bulletin data painted onto those regions: **the choropleth** — the coloured fill carrying the danger rating — plus the dissolved outer boundary of every micro-region that shares one bulletin on the selected day. Shows how the warning service actually grouped terrain that day: two regions inside one outline are covered by one text. Not a layers-menu row: it has its own five-step strength control on the map (below). | **50%** |
| **Minor regions** | L2 | Sub-region grouping of micro-regions (e.g. `CH-41`, Lower Valais). Outline only. | Off |
| **Major regions** | L1 | Top-level area (e.g. `CH-4`, Valais). Outline only. | Off |

The **micro-region choropleth is the map's core**: each L4 region is
filled with the colour of its peak danger rating for the selected date.
Regions with no bulletin that day are left uncoloured (`no_rating`).
Selecting a region — by tapping the fill, tapping a resort pin inside
it, or picking it from search — opens the detail surface and outlines
the region.

**Why the geography and the bulletin fill are separate** (SNOW-656). The
geography is fixed and date-independent; the data painted onto it changes with
every scrubbed day. Separating them lets the borders stay up throughout, since
they never interfere with anything, while the infill can yield to the
downloaded-areas overlay — which paints translucent squares over the very same
polygons and is unreadable on top of a coloured fill. **The fill and the
downloads panel's own map switch ("Display on the map") can never both be
on**, and the two controls mirror each other:
[`decisions/bulletins-yield-to-downloaded-areas.md`](decisions/bulletins-yield-to-downloaded-areas.md).

**The fill's strength is the user's choice, in five steps** — 0, 25%, 50%
(default), 75%, 100% — from a roundel in the bottom-right stack whose flyout
opens to the left. 0 is the off position, so the control subsumes the on/off
toggle it replaced rather than sitting beside one. The choropleth is
translucent again as a result, which reverses an earlier decision and brings
back its basemap drift knowingly:
[`decisions/bulletin-fill-is-a-user-choice.md`](decisions/bulletin-fill-is-a-user-choice.md).

At step 0 the fill goes *transparent*, not absent — it is the layer region
taps resolve against, so borders stay tappable with the colour gone. Only when
step 0 meets **Micro regions off** is it removed outright.

The L1/L2 outlines are orientation aids: they let a visitor see which
larger area a micro-region belongs to without leaving the map. The
**bulletin groupings** boundary inside the Bulletins row is the one derived
tier — it is not a fixed administrative boundary but a per-day artefact
showing which micro-regions the forecaster treated as one unit, and so it
changes as you scrub through the season.

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
- **Pin size carries the tier (SNOW-543).** Each resort has a curated
  `tier` — Core, Standard or Minor — and the pin radius is interpolated
  from it, so Zermatt and a one-lift village hill no longer read as
  equally important. The tier is stored, not derived: the resort-tiering
  review's finding is that scale is the wrong axis, since small areas high
  in the Alps (Avers, Bivio, Arolla) carry more avalanche decision-making
  per visitor than a large low resort, and piste km would rank them last.
  Colour stays constant, reserved for the orthogonal "kind of resort" axis
  — the map's colour budget is already committed to the danger scale.
  **Every tier draws at every zoom.** The review proposed suppressing
  Minor below a zoom threshold; for this product that is backwards, since
  the small resorts are frequently the ones with the most interesting
  terrain. Size does the ranking; nothing disappears.
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

### 3.4 Weather — condition symbols and temperature (SNOW-573)

The **Weather** overlay draws a condition symbol (a Meteocons icon —
clear, cloudy, rain/snow at three intensities, fog, thunder — with a
day/night variant) plus the day's max temperature at each forecast point:
every resort, and, for a signed-in visitor, every one of their own
favourite pins too.

- **What it shows.** One symbol per point for whichever date the scrubber
  is showing. Point weather is forecast-only — it covers today plus the
  next several days, and **often fewer than the full window** (the
  backing weather model can return a shorter run than requested; a short
  window is the normal case, not a failure). Scrubbing to a date outside
  the stored window disables the layer's menu row, with a reason, rather
  than silently drawing nothing.
- **Privacy.** The public data (every resort's symbol) is not
  authentication-gated, but a **favourite's** own weather is private —
  it only ever reaches the map for the signed-in visitor it belongs to,
  merged in client-side alongside the public resort data. Another user's
  favourite-anchored weather is never part of the public payload.
- **Who can use it.** Everyone can see resort weather once the feature is
  enabled site-wide; a signed-in visitor additionally sees weather on
  their own favourite pins. **Off by default**, like community reports.
- **Rollout.** Gated behind the `weather_layer` feature flag during
  rollout (see [`feature-flags.md`](feature-flags.md)).

### 3.5 UGC eligibility at a glance

| Surface | Visibility gate | Create/write | Default overlay state | Data class |
|---------|-----------------|--------------|-----------------------|------------|
| Favourites | Signed in | Owner only, 10/min | On (eligible users) — switched from the favourites panel, not the layer menu | Private, per-user |
| Resorts | Public | Staff via `edit_map` editor | Off | Shared reference |
| Community reports | Public | via Report flow below | Off — switched from the field-observation panel, not the layer menu | Anonymised, public |
| Weather | Public (resorts) + own favourites (signed in) | n/a — derived from the weather pipeline | Off | Public + per-user merge |
| Report (submit) | Signed in, verified + location | The reporter | n/a (a control, not an overlay) | Raw observation |

`edit_map` remains a superuser-scoped feature flag during rollout;
[`feature-flags.md`](feature-flags.md) is the operator reference.

---

## 3.6 What the layer menu lists (SNOW-658)

The layer menu (the stacked-layers roundel) is the map's **view controls for
published data**. Its sections say what their rows are:

| Section | Rows | What a row switches |
|---------|------|---------------------|
| **Bulletins** | SLF (CH), MétéoFrance (FR), ALBINA (AT, IT) | One warning service's bulletins, over every country it publishes for |
| **Boundaries** | Major (EAWS Level 1), Minor (EAWS L2), Micro (EAWS L4) | One tier of the EAWS region hierarchy |
| **Locations** | Resorts | Named places geocoded onto regions |
| **Conditions** | Weather (flag-gated) | Forecast symbols at each point |
| **Base map** | one row per basemap | The geographic backdrop |

**Favourites and community reports are not in it.** Both are
user-generated, and each already has a roundel of its own, so each toggle
lives in the panel that roundel opens — as the footer switch labelled
"Display on the map", the same wording on all three UGC panels (SNOW-658)
— alongside the list of what the user has saved and the control to add
another. That is the pattern SNOW-634 set
for offline downloads, generalised: one subject, one way in. Their
offline-status dots did not move with them; see
[`offline-map.md`](offline-map.md).

---

## 3.7 The three overlay panels, and why they differ by viewport

Downloads, Favourites and Field observations each open a panel from their
own roundel in the bottom-right stack. The three share one shape — a
header carrying the roundel's own mark, a list of what the user has, an
"add" call to action, and a footer switch labelled "Display on the map" —
and **two layouts**, chosen by viewport width at the `sm` breakpoint
(640px).

**Below `sm`, a panel is a docked sheet**: full-width, anchored to the
bottom edge, covering the map beneath it. That is the right shape on a
phone. The list is the thing being read, a phone has no room to show it
beside anything, and bottom-docked puts it under the thumb — the same
convention every native mobile sheet uses. A panel inset into a 375px
viewport would be a small box floating in a map the user cannot see much
of either.

**At `sm` and above, a panel is inset**: width-capped and positioned clear
of the season scrubber below it and the roundel column to its right, so
the map stays usable beside it. On a desktop the map is large enough that
covering it would be the loss, not the list — a user comparing a
favourite against the danger fill under it needs both on screen at once.
The inset is measured at open time from the real furniture rather than
guessed, so it stays correct as the scrubber and the roundel stack change
size (`static/js/map_overlay_bounds.js`).

**This difference is deliberate, and it is not a bug in either direction.**
"The panel fills the screen on my phone" and "the panel doesn't fill the
screen on my laptop" are both the intended behaviour. Both layouts are
bounded and scroll internally: a panel never grows past the viewport and
never pushes its own content off-screen, whatever the list contains.

## 3.8 Overlay roundel state — is it on the map right now?

Each of the three roundels shows whether **its own** overlay is currently
drawn: a coloured rim, in the same colour as the ON position of the
"Display on the map" switch that controls it, plus the same fact in the
roundel's accessible label ("Your favourites — shown on the map"). One
signal, one meaning, on all three.

It is live rather than a snapshot: the rim follows the panel switch, and
also follows anything else that takes an overlay off the map — turning
Bulletins on switches the downloaded-area squares off, because the two
paint the same polygons, and the roundel says so without the panel being
open. It survives a basemap switch, which re-draws every layer.

**The rim states what is drawn, not what was asked for.** Switch an
overlay on with no data to draw — offline, first enable, a fetch that
fails — and the switch stays ON (the setting took, and will be restored
next time) while the rim stays off, because nothing reached the map. The
two disagreeing is the point: it is the only way to see that a request
has not landed. Positioning a pin clears every overlay off the map for
the duration of the placement, and all three rims go out with them and
come back when it ends, for the same reason.

The downloads roundel used to carry a different signal — "this device
holds at least one downloaded area" — which is a fact about storage, not
about what is on the map. It was removed rather than kept alongside: two
meanings on one roundel is unreadable, and the downloads panel answers the
storage question properly with a list, per-area sizes and a budget bar.
See [`offline-map.md`](offline-map.md).

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

Each basemap also has an **identity colour**, shown wherever a downloaded
area is — the download roundels' fill and a swatch on each row of the
"Manage downloads" sheet — so a device holding several downloaded areas
under different basemaps can tell them apart. Both download roundels (the
per-region one, and the custom-area one in the bottom-right stack) always
paint the ACTIVE basemap's colour, never a basemap some earlier download
happened to use — a control sitting on the Swisstopo map is never painted
in Standard's colour, whatever is stored underneath it. Per-area basemap
identity is the "Manage downloads" sheet's job, one tap away. The
per-region download roundel goes one step further: switch basemap after
downloading a region and its roundel doesn't just go quiet — it shows a
distinct "downloaded, but for another basemap" ring, in that OTHER
basemap's colour, that a tap turns into a download for the basemap now
showing. Nothing was lost by switching; the roundel now says so instead of
reading as data loss. See [`offline-map.md`](offline-map.md)'s "Download
basemap" and "Manage downloads" sections for the mechanism.

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
  never thrashes during a drag or playback). The Weather overlay
  re-projects for each scrubbed date too, but with no network round-trip
  at all — its payload already carries the whole forecast window, so a
  date change just re-reads the already-fetched data.
- **Performance.** The full season's ratings are fetched once, on the
  first scrub, and served from memory thereafter — the first scrub pays a
  single round-trip; every later scrub and all playback render instantly.
  A cold map load fetches one day's ratings when the URL names one, and
  none at all when it does not, keeping first paint small.
- **Shareable.** The scrubbed date is reflected in the URL (`/?d=YYYY-MM-DD`),
  so a specific day's map can be linked and reloaded. Since SNOW-660 that
  includes today: an empty querystring means "no day chosen", and the map
  opens uncoloured with the date ribbon saying so, rather than painting a
  day the visitor never asked for.

---

## 6. Other behaviours

**Search.** The header hosts a client-side autocomplete over the regions
and resorts already loaded, so there are no extra round-trips. Matching
is accent-insensitive, prefix matches rank above substring matches, and
each result carries a "Region" or "Resort" badge to disambiguate places
whose region and resort share a name (e.g. Davos). Selecting a result
routes through the same selection path as a map tap.

**Placing a pin clears the map.** Positioning a pin is a precision task, so
while it is in progress the map shows nothing but the basemap and the pin
being placed: the choropleth, every region outline, bulletin groupings,
resort pins, other people's reports, existing favourites, and any open
popup all disappear. The instant the pin is submitted or the flow is
cancelled, every layer returns exactly as it was — an overlay the visitor
had switched off stays off. This applies to all three placement flows:
adding a favourite, filing a field observation (from the point at which
you start setting the location by hand, or refine a GPS fix), and the
staff Edit-resorts tool (from dropping the draft pin until Save or
Cancel).

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

- Favourites, community-reports, and weather overlays get a client-side
  write-through cache so they still render offline once fetched
  (favourites never expire on read-back; community reports re-apply their
  48-hour age filter; a cached weather payload simply stops drawing
  anything as scrubbed dates roll past its forecast window). When an
  overlay can't load and nothing is cached, a small "unavailable offline"
  toast explains why rather than the toggle looking broken.
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
