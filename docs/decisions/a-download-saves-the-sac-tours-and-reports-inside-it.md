---
name: a-download-saves-the-sac-tours-and-reports-inside-it
description: Area download saves SAC tours and community reports — tourBoxes, tourPlan, reportsInBox, items.tours, 600/m tour read
status: current
last-reviewed: 2026-11-04
---

# A download saves the SAC tours and community reports inside it

**Decision (SNOW-1417).** Hugo's promise for a downloaded area is that
everything works inside it, SAC tours and community reports included. A
download and every content sync now:

- **SAC tours.** Cut the area's rectangle into boxes the tours endpoint
  answers (`tourBoxes` in `basemap_download_core.js`; at most
  `SKI_NETWORK_MAX_SPAN_DEG`, 0.5°, each way, with two thousandths of slack
  for rounding). Fetch each box, plus `?overview=1`, page-side; take each
  tour once by uuid (`tourPlan`); and warm the box answers and each tour's
  `feature.json` and `terrain.json`. A failed or `truncated` box makes the
  plan **short**, so the area is never stamped complete over part of its
  tours.
- **Community reports.** The feed is already fetched into the overlay cache
  on every run (`cacheOverlayFeedsForDownload`). The reports whose point is
  inside the rectangle are stamped by id (`reportsInBox`), with the feed's
  read time and the newest report's `observed_at`.

The stamp (`stampContentRecord`, through `withPublicItems`) carries
`items.tours` (`{uuid, name, urls}`), `items.tourBoxes` (the box urls the
run warmed), `items.reports` (`{id}`), `reportsAt` and `reportsNewestAt`.
They are every reader's, so a signed-out sync writes them too, and a sync
that did not read one keeps the record's.

**Offline.** The tours layer (`ski_tours.js`) loads by view rectangle,
which offline was never fetched. Offline, or when the view's own fetch gets
no answer, it reads the saved boxes that overlap the view
(`savedBoxesFor`, `mergeAnswers` in `ski_tours_core.js`) from the
`tourBoxes` each downloaded area carries (`pwaBasemapAreas.downloadedAreas`).
The worker classifies the tours feed and the two tour files as
`public-json`, so they are network-first and read back by exact url.

**Verification.** `/offline/`'s conditions stage counts a tour as saved
only when both of its files are held, and a report only when its id is
still in the overlay cache's `community_reports` row, which is IndexedDB,
not Cache Storage.

## Why

**Boxes, not the area's rectangle.** The endpoint refuses a box wider than
0.5° and caps an answer at `SKI_TOURS_MAX_FEATURES`. Many custom areas are
wider than 0.5°. Equal boxes under the cap, sharing their edges, cover the
area exactly, and a tour on a seam is in both answers and is taken once.

**`items.tourBoxes` is stamped, not recomputed.** The offline layer must ask
for exactly the urls the download warmed. Reading them off the record means
a later change to the cut cannot leave a device asking for boxes it never
saved.

**Tour files are permanent.** A tour's Feature and profile change only when
SAC edits the tour, so a sync fetches them only when missing
([sync-fetches-only-what-changed.md](sync-fetches-only-what-changed.md)).
The box answers are perishable: they are what shows a new tour.

**The tour read budget.** `routes:ski_tour_feature` was limited to 60 a
minute per IP, the draw preview's budget. An area can hold well over a
hundred tours, so a download hit the limit, most tours were refused, and
the area never synced complete. The limit exists for the terrain walk
(`?sample=1`), which keeps 60 a minute. A plain read, which serves the
stored record, gets 600 a minute (`_SKI_TOUR_READ_RATE`).

## Consequences

- The size estimate is unchanged. It prices the tiles and a fixed documents
  allowance and has never counted content urls, and an area's tours are not
  known until its boxes answer.
- A tour that was never sampled is saved without its profile, as the panel
  first opens it online; the `?sample=1` refill needs signal.
- The tours urls appear on `#map` only under the `route_drawing` flag, as
  the layer does, so a download saves tours only where the reader can see
  them.
