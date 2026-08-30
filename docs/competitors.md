---
name: competitors
description: Competitor list — WhiteRisk, SnowSafe, Whympr, OpenSnow — with feature profiles and the feature ideas each one suggests for Snowdesk
status: current
last-reviewed: 2026-08-30
---

# Competitor list

**Last automated competitor scan: 2026-08-30.** News, product-update and
new-entrant findings from that pass are marked inline as "2026-08-30 scan
update"; the full source list is in the PR description for that change.
Findings from the prior 2026-08-23 and 2026-08-19 passes remain marked as
such below.

Apps and sites that overlap Snowdesk's job — *"tell me what the avalanche
bulletin says for where I'm going today, and let me act on it"*. Each
profile records what the product actually does (checked against its own
documentation and store listing on the date given), not what we assume it
does.

The list exists to answer two questions: **where a user would go instead
of Snowdesk**, and **which of their features are worth stealing**. The
feature ideas are collected in [Feature inspiration](#feature-inspiration)
at the end rather than scattered through the profiles, so that section
reads as a candidate backlog.

Positioning and the reasoning behind Snowdesk's own product shape live in
[`design-system.md`](design-system.md) and
[`user-journeys.md`](user-journeys.md); this document is only the outside
view.

| Product | Built by | Scope | Our overlap |
|---|---|---|---|
| [WhiteRisk](#whiterisk) | SLF (CH) | Canonical Swiss avalanche platform | Highest — same bulletin, same author |
| [SnowSafe](#snowsafe) | First Line Solutions (AT) | Focused bulletin app | Direct — same job, adjacent regions |
| [Whympr](#whympr) | Whympr (FR) | All-in-one trip planner | Partial — bulletin is one tab |
| [OpenSnow](#opensnow) | Cloudnine Weather (US) | Snow-forecast platform | Adjacent — forecast-led, US-centric |

---

## WhiteRisk

**The canonical avalanche app.** Built by the WSL Institute for Snow and
Avalanche Research SLF — the same institute that writes the Swiss
bulletin we ingest. It is not a third party interpreting an official
product; it *is* the official product, and it sets the standard every
other app in this list is measured against, including ours.

- **Checked:** 2026-08-19 — <https://www.slf.ch/en/services-and-products/white-risk-app/>,
  <https://www.slf.ch/en/services-and-products/white-risk-portal/>,
  <https://www.whiterisk.ch/>. **Re-scanned 2026-08-19** (routine competitor
  scan) via web search — <https://www.slf.ch/en/news/white-risk-the-new-look-slf-app/>,
  <https://www.bergundsteigen.com/en/artikel/reporting-avalanches-made-easy-with-the-new-white-risk-design/>
  (direct fetch of both blocked by this session's network policy; findings
  below are corroborated across both independent sources, not primary-verified
  — re-verify by direct fetch next pass). **Re-scanned again 2026-08-23**
  (routine competitor scan) via web search — no material change since
  2026-08-19: the 20th-anniversary/Best-Swiss-App-2025 coverage and the 2026
  redesign (Météo-France bulletin, crowd-sourced observations, crux display)
  are the same findings already recorded below; no new feature or
  business-model news surfaced this pass. Direct fetch of slf.ch and
  whiterisk.ch is still blocked by this session's network policy.
  **Re-scanned again 2026-08-30** (routine competitor scan) via web search —
  no material change since 2026-08-23; the 2026 redesign details recorded
  below remain current, and no funding, team or partnership news surfaced.
  Direct fetch of slf.ch and whiterisk.ch is still blocked by this session's
  network policy.
- **Shape:** a web portal plus a companion iOS/Android app, four
  languages, organised into four modules:
  - **EXPLORE** — the avalanche knowledge base: text, images and
    animations covering the whole domain. Free.
  - **LEARN** — structured e-learning with per-lesson objectives,
    exercises and an end-of-lesson quiz, cross-linked into EXPLORE. One
    lesson ("Avalanche danger ratings and warning signs") is free.
  - **TOUR** — planning. Draw a route along paths, marked ski routes or
    freely across terrain; **WhiteRisk analyses the drawn route and marks
    critical cruxes with the relevant avalanche and terrain
    information**. Plans sync between web and app, forming a personal
    tour library.
  - **PRO** — instructor and professional tooling: a Desktop Presenter,
    a Media Explorer of visuals for teaching, and discounted student
    licences.
- **In the field:** GPS position on the tour map, an **Analyser** for
  assessing a slope on site, offline downloads of user-defined map areas,
  and free access to snow and temperature readings from the automated
  measuring-station network.
- **Maps:** topographic coverage for Switzerland, France and Austria with
  layers including **slope angle** and **avalanche terrain**; the free
  tier drops to OpenStreetMap.
- **2026 update (new app design):** a redesign shipped for the 2025/26
  season adds (a) **the Météo-France bulletin** — WhiteRisk now publishes
  the French Alps/Pyrenees/Corsica avalanche bulletin alongside the Swiss
  one, not just French topo maps as before; (b) a **crowd-sourced avalanche
  observation form** — any user can mark an avalanche's starting point on
  the map and log its size and type from the field; and (c) clearer **crux
  display** on planned tours (automatically-flagged critical passages, with
  an improved interpretation aid) plus an **elevation profile and estimated
  duration for alternative routes**. WhiteRisk was also named Best Swiss
  App 2025.
- **Business model:** free tier (EXPLORE, one LEARN lesson, TOUR on OSM
  maps); **Standard CHF 29/yr** for full EXPLORE + LEARN + TOUR including
  all topo maps and layers; **Pro CHF 58/yr** adding PRO.
- **Where it is stronger:** everywhere it chooses to compete. Authoritative
  by construction, terrain-aware route analysis, station data, teaching
  material, and a decade of institutional trust.
- **Where the gap is:** it is still **Switzerland-first**, and the gap
  narrowed this year — the 2026 redesign added the Météo-France bulletin
  outright (see above), so WhiteRisk is no longer single-provider. Austria
  and Italy still get map coverage but not a first-class ingested bulletin,
  and there is still no single normalised render across CH, FR, AT and IT
  side by side — WhiteRisk shows two bulletins in one app, not one model
  across three-plus providers the way our render model does. It is also
  still a *platform* with modules, licences and an account — the opposite
  of a URL you open on a phone in a lift queue. **Watch this**: adding
  ALBINA (AT/IT) next would close the gap our whole positioning rests on.
  Our answer to WhiteRisk is not "more features" but **cross-provider
  normalisation and zero friction**.
- **Note:** WhiteRisk is also our structural reference — see "Why the page
  renders the bulletin whole" in [`design-system.md`](design-system.md).
  Treating it as both benchmark and competitor is deliberate: we follow the
  same canonical SLF/EAWS layout, because neither of us invented it, and we
  render the provider's bulletin in full rather than a subset of it.

## SnowSafe

- **What it is:** a focused avalanche-bulletin app (iOS/Android) by First
  Line Solutions GmbH. Explicitly *not* a planner: "all avalanche
  information at your fingertips."
- **Checked:** 2026-08-19 — <https://snowsafe.at/>, App Store listing
  (v6.3.0810, released Aug 2026). **Re-scanned 2026-08-23** (routine
  competitor scan) via web search — Google Play/Uptodown changelogs surfaced
  are for v6.1.0122/v6.1.0108 (Jan 2026: refined clinometer, cleaner
  interface, smarter weather calculation), which predate the v6.3.0810
  release already recorded above. No material change found this pass.
  **Re-scanned again 2026-08-30** (routine competitor scan) via web search —
  v6.3.0810 remains the current release; no newer version or business news
  found. Direct fetch of snowsafe.at and the App/Play Store listings blocked
  by this session's network policy.
- **Overlap with Snowdesk:** the closest in scope of anything here. Same
  job, same editorial instinct, overlapping regions — its Austrian, South
  Tyrolean, Trentino and Veneto coverage is the same EAWS/ALBINA family
  we ingest.
- **Coverage:** ~17 regions — the Austrian LWDs (Vorarlberg, Tirol,
  Salzburg, Steiermark, Kärnten, Ober-/Niederösterreich), Bavaria,
  Alto Adige/Südtirol, ARPA Veneto, Trentino, Slovenia, Slovakia, Czechia,
  Poland, Iceland, Norway (Varsom). Notably **no Switzerland and no
  France** — the two providers where we are strongest.
- **Where it is stronger:**
  - **Push alerts** — notifications on high danger and on a new bulletin
    being issued mid-day. v6.3 added push specifically for danger levels.
  - **Weather-station map layer** (v6.3) — station data with interactive
    charts and filters, plus dynamic region borders.
  - **Field instruments** — a clinometer for slope measurement, and an
    aspect compass pairing slope aspect with the day's danger patterns.
  - **Weather** — MetGIS V2 mountain forecasts, with a "Professional"
    forecast tier as the paid upgrade.
  - **Offline** — bulletins sync when there is signal and are fully
    readable without it.
- **Where it is weaker:** no map-first entry point of the kind we lead
  with, no bulletin history or season view, no route handling, and no web
  surface at all. Ratings are thin enough that the App Store shows no
  aggregate.
- **Business model:** free app; "Professional Weather Forecast" at
  $3.99/month or $29.99/year. The bulletin itself is never paywalled.
- **Read:** the product to watch. Its design principle — *as simple and
  reliable to operate as a transceiver* — is the same instinct behind our own
  restraint, though we reach it differently: simplicity of *operation*, over a
  bulletin we still render in full.

## Whympr

- **What it is:** an all-in-one mountain trip-planning app (iOS/Android +
  web), born in Chamonix. Hiking, ski touring, mountaineering, climbing,
  trail running, snowshoeing.
- **Checked:** 2026-08-19 — <https://get.whympr.com/en>, App Store listing
  (v2.38.1, 4.4★). **Re-scanned 2026-08-19** (routine competitor scan) via
  web search — <https://get.whympr.com/en/blog-articles/trail-difficulty-ratings-for-every-hiking-path-all-on-one-map>
  (direct fetch blocked by this session's network policy). **Re-scanned
  2026-08-23** (routine competitor scan) via web search (Tracxn, PitchBook,
  Crunchbase, Dealroom) — total raised remains $443K (BigBooster, HUB612,
  SKEMA Entrepreneurs, Sowefund), ~18 employees; no new funding round, team
  move, or product feature surfaced this pass. **Re-scanned again
  2026-08-30** (routine competitor scan) via web search — no new funding
  round or major feature found (still ~18 employees, $443K total raised).
  Surfaced two pre-existing details not previously recorded here: a free,
  no-login community field-reporting feature (reports auto-expire after 90
  days unless corroborated by another user) and confirmation that
  geolocated bulletin coverage already spans FR/CH/IT/AT/ES/AD/US/CA/
  Scotland (folded into the community bullet below). Direct fetch of
  get.whympr.com and Tracxn still blocked by this session's network policy.
- **Overlap with Snowdesk:** the avalanche bulletin is *one tab* of a
  planning app. Geolocated bulletins for FR, CH, IT, AT, AD, ES, US, CA
  and Scotland — broader country coverage than our three providers, but
  presented as a pass-through of the official product rather than a
  normalised, re-rendered one.
- **Where it is stronger:**
  - **Route corpus** — 100,000+ routes and POIs from Skitour, Camptocamp
    and tourism offices, filterable by activity, difficulty and ascent,
    with paid "Pro Topo" guidebook routes on top.
  - **Terrain layers** — slope-gradient shading across European regions,
    plus nature-conservation zones and livestock-guardian-dog (*patou*)
    locations. (June 2026) two new **hiking trail-difficulty layers**
    ("Hiking – trails"), colour-coding every mapped trail worldwide by
    difficulty, with an explicit caveat that a rating can shift a full
    grade with season or weather — the same "don't let the layer replace
    judgement" framing our own disclaimer takes.
  - **Maps** — 11 national topo maps (IGN France, SwissTopo, Fraternali),
    3D/LiDAR terrain, 30 cm winter satellite imagery of the Mont Blanc
    massif, and (v2.38.1, Aug 2026) offline area downloads with a chosen
    map and layer set.
  - **Weather** — Meteoblue partnership; freezing level, sunshine hours,
    past conditions. 23,000+ webcams.
  - **Community** — trip reports carrying current conditions, an activity
    wall of the last ten days of outings, GPS tracking and a logbook.
    *(2026-08-30 scan update)* field reports are free to post without a
    login or subscription, and auto-expire after 90 days unless
    corroborated by another user — a lightweight staleness/trust mechanic
    [Feature inspiration](#feature-inspiration) item 4 doesn't have yet.
  - **Hardware** — Apple Watch, Garmin and Suunto integrations.
  - **AR peak viewer.**
- **Where it is weaker:** the bulletin itself is a thin surface. No
  evidence of cross-provider normalisation, of bulletin history, or of
  anything that reads the bulletin *for* you. It is a planning app that
  displays a bulletin, not a bulletin product.
- **Business model:** Premium ($29.99/yr, $14.99/3 months) plus
  à-la-carte content — Pro Topos $1.99–$29.99, Italian maps $24.99, an
  "Outdoor Pack" $34.99. The free tier is real, but map downloads and the
  richer filters sit behind Premium.
- **Read:** the closest thing to a "do everything" incumbent in our
  geography. Not competing on bulletin quality — competing on being the
  only app you open before a tour.

## OpenSnow

- **What it is:** a snow-and-weather forecast platform (iOS/Android +
  web) by Cloudnine Weather, Inc. Forecast-led rather than bulletin-led:
  the question it answers is *where will it snow*, not *is it safe*.
- **Checked:** 2026-08-19 — search results and a third-party review;
  opensnow.com blocks automated fetches, so pricing below is second-hand
  and worth re-checking before it is quoted anywhere externally.
  **Re-scanned 2026-08-19** (routine competitor scan): OpenSnow published
  a 2026/27 El Niño winter preview forecast (per POWDER and Adventure
  Sports Network coverage) — a named-meteorologist seasonal outlook piece,
  consistent with the Daily Snow editorial thesis already recorded below;
  no funding, team or acquisition news found this pass. **Re-scanned
  2026-08-23** (routine competitor scan) via web search — found one genuine
  product update the 08-19 pass missed: OpenSnow shipped **PEAKS**, a new
  in-house forecast model, for the 2025/26 winter season, claimed (per
  OpenSnow's own marketing and app-store copy) to be up to 50% more accurate
  than prior sources in mountain terrain, and now powering every
  precipitation/temperature/wind forecast in the app — added to "Where it is
  stronger" below. App version 7.4.0 (30 Apr 2026) gates 11–15 day
  forecasts, the global storm map, super-res radar and severe-weather
  alerts behind Premium. No funding, team or acquisition news found this
  pass either. **Re-scanned again 2026-08-30** (routine competitor scan) via
  web search — app version remains 7.4.0, no newer release found. The only
  new item in the window is an annual **2026-27 winter forecast preview**
  (published 14 Aug 2026, El Niño outlook), routine Daily Snow seasonal
  editorial content rather than a product or business change, so not
  recorded as a separate finding. No funding, team or acquisition news this
  pass.
- **Overlap with Snowdesk:** adjacent rather than direct. Its avalanche
  forecasts cover the US and Canada only, so it does not compete in the
  Alps today — but its *shape* is the most commercially proven in this
  list, and the parts worth copying are editorial, not geographic.
- **Where it is stronger:**
  - **PEAKS forecast model** *(new, 2026-08-23 scan)* — an in-house
    mountain-terrain forecast model, claimed up to 50% more accurate than
    prior sources, now powering every precipitation/temperature/wind
    forecast in the app. A reminder that forecast accuracy itself is a
    competable surface, not just presentation — relevant to our own
    Open-Meteo dependency in `apps/weather/`.
  - **Daily Snow** — a human-written daily forecast narrative per region,
    authored by named local meteorologists, for the US, Canada, Europe,
    Australia and New Zealand. This is the product's spine: people
    subscribe to a *writer*, not to a data feed.
  - **Threshold alerts** — "tell me when this resort is forecast to get
    more than N cm", rather than a fixed notification.
  - **Comparison UI** — side-by-side snow and weather across several
    mountains at once.
  - **History** — up to 10 days of past snowfall, snow line and weather
    per mountain.
  - **Layers** — 3D maps for snow depth, avalanche risk, air quality,
    wildfire smoke and fire perimeters; radar; 15-day hourly forecasts;
    offline resort trail maps.
  - **Year-round utility** — precipitation, wind and smoke forecasts keep
    the app installed through the summer.
- **Where it is weaker:** it is a weather product, and the avalanche layer
  is a bolt-on. No bulletin normalisation, no EAWS problem model, no
  European avalanche coverage. Reviewers note accuracy falls away beyond a
  short horizon and that the app is of limited use to anyone not chasing
  fresh snow.
- **Business model:** the aggressive one. A free tier including avalanche
  forecasts, then (2026) **Base $49.99/yr** (family $89.99) and
  **Premium $99.99/yr** (family $179.99) — a rise from the older
  All-Access tier at ~$29.99/yr, and evidence that a subscription in this
  category can carry a much higher price than the CHF 29 / $29.99
  benchmark the European products sit at.
- **Read:** proof that editorial voice and alerting, not raw data, are
  what people pay for.

---

## Not yet profiled

Names worth a profile when someone has an hour, kept here so the list
grows deliberately rather than by whoever last read a blog post. **These
lines are recall notes, not research** — each is an unverified one-liner
saying why the product might matter. Promote one to a full profile above
only after checking it the way the four profiled products were checked,
and delete its row here when you do.

Ordered by how much a profile would probably change our thinking.

| Product | Who | Why it might matter |
|---|---|---|
| **AvalancheClarity** | Simon Perry (guidebook author, Tignes) — new find, 2026-08-19 scan | **Escalate to top of the profile queue, ahead of Skitourenguru — 2026-08-23 scan update found this is materially bigger than previously recorded.** A free app + web widget that AI-translates and plain-language-explains official avalanche bulletins, works offline, and — per PlanetSKI and SnowBrains coverage — sends a push notification the moment a new bulletin or a revision is published for a subscribed region ([Feature inspiration](#feature-inspiration) item 1, already shipped by someone else). On 30 March 2026 it also launched an embeddable widget (one line of code, accepts GPS coordinates, region codes or a multi-point touring route) — a distribution channel we don't have an answer to. **What's new this pass:** per PlanetSKI's 5 Apr 2026 coverage (missed by the 2026-08-19 scan), the app is no longer France-only — it now covers **every avalanche bulletin area in Europe, 14 countries**. France gets full AI translation across all 36 massifs (12 fully translated, the rest shown in original French with interactive weather charts); **Switzerland gets the same full AI-translation treatment — all 134 SLF micro-regions, in eight languages** — i.e. it already covers our SLF provider end-to-end, not just Météo-France. Austria, Italy, Germany, Norway, Sweden, Spain, Andorra, Slovenia, Slovakia, Czechia, Finland and Scotland get tap-through map links to the official bulletin (pass-through, not translated) — which includes our ALBINA (AT/IT) territory. It also indexes **1,100 searchable European ski resorts** and a per-user "watchlist" spanning France/Switzerland/Italy bulletins in one place. This is the cross-provider consolidation idea we called our unclaimed differentiator ([Feature inspiration](#feature-inspiration) item 2) — someone has now shipped a rough version of it (translation-depth varies by country, but the single-app, multi-provider coverage is real) across all three of our providers' geographies. Direct fetch of avalancheclarity.com, planetski.eu and the SnowBrains writeup remains blocked by this session's network policy, so this entry is search-corroborated across independent sources (PlanetSKI, SnowBrains, snowheads.com, stylealtitude.com, the Google Play listing) but not yet primary-verified — **prioritise a direct-fetch verification and full profile next pass**, this has outgrown a one-liner. **2026-08-30 scan:** still no material change beyond the 2026-08-23 findings above; avalancheclarity.com and its Play Store listing remain blocked to direct fetch for a third consecutive pass — this needs a manual/non-automated verification pass rather than another scripted scan. |
| **Skida (Alpine Adventures)** | Skida AS (NO) — new find, 2026-08-30 scan | Norwegian ski-touring app (founded 2023, ~4 staff, $429K raised — ESA BIC Norway, TheFactory, K2 Gründerlab) that, like AvalancheClarity, **pulls together official bulletins from multiple national providers in one app without altering their content** — Varsom (Norway), WhiteRisk (Switzerland) and Météo-France are named sources, with steepness-map coverage confirmed across Austria too, i.e. overlapping our SLF and Météo-France territory and touching our ALBINA one. It renders each avalanche problem as an aspect/elevation **"pizza" diagram** (compass slices for aspect, a mountain-icon axis for elevation) — a live, shipped example of [Feature inspiration](#feature-inspiration) item 8 (SnowSafe's aspect compass), independent of SnowSafe. A second data point, after AvalancheClarity, that cross-provider normalisation is no longer an unclaimed idea — worth a full profile alongside it. Direct fetch of skida.app and its store listings blocked by this session's network policy; found via skida.app's own `/avalanche-bulletins-2/` page description and Pitchbook. |
| **onX Backcountry** | onX Maps (US) — new find, 2026-08-23 scan | Not a bulletin competitor (US/Canada only, no Alps coverage) but relevant to [Feature inspiration](#feature-inspiration) item 6 (slope-angle overlay): it ships an **Avalanche Terrain Exposure Scale (ATES)** map layer — colour-coded terrain classification plus a distinct "safe zone" rating marking areas with no avalanche exposure regardless of conditions — alongside slope-angle, slope-aspect and an "avalanche simulator" tool, and has a formal partnership with the American Avalanche Association feeding forecasts from local avalanche centres into the app. Worth a look as prior art for how a terrain-hazard layer earns its safety framing, given our own disclaimer concerns about a slope-angle layer inviting "the map said it was fine" misreadings. **2026-08-30 scan:** no material change — the ATES/safe-zone expansion traces to a 12 Nov 2025 announcement, already reflected above; onxmaps.com remains blocked to direct fetch. |
| **Skitourenguru & Yéti** | Günter Schmudlach (CH) / Petzl Foundation-backed (FR) | The most directly threatening idea in the category: both rate individual ski tours by avalanche risk **daily**, by combining the official bulletin with terrain analysis over a route database. This is [Feature inspiration](#feature-inspiration) item 2 as a shipped product — now shipped twice. **2026-08-19 scan update:** Petzl Foundation funding has made both **free to all users** (previously gated); the combined route database is now ~13,000 tours across the Alps (~4,600 in the French Alps alone), with ~500 more planned for the Pyrenees during 2026, and a Petzl-produced tutorial video series is rolling out explaining how bulletin + terrain data combine. Yéti is the newer, France-only sibling product (Météo-France bulletin + DTM terrain analysis) — worth its own row once profiled, currently bundled here since the two are developed and funded together. Profile Skitourenguru first as originally planned, but note Yéti alongside it. **2026-08-30 scan:** no material change to the Skitourenguru/Yéti picture itself, but found one relevant detail not previously recorded — Skitourenguru's terrain-hazard methodology (ATHM) is licensed into **Outdooractive** as a map layer, giving a general-purpose tour portal the same bulletin-plus-terrain scoring this list treats as Feature inspiration item 2 (see the Outdooractive row below). |
| **avalanche.report** | EUREGIO / ALBINA | The provider's own web front end — one of our three upstreams. Sets the expectation for how an ALBINA bulletin "should" look, and its region map is the thing our AT/IT users already know. 2026-08-19 scan found no material product changes since last review — only a backend admin-GUI fix, not user-facing. 2026-08-30 scan found nothing further — only the standing project infrastructure (GitHub repos, EAWS governance). |
| **Regobs / Varsom** | NVE (NO) | State-run observation-plus-forecast platform at national scale — still the reference design for `FieldObservation` if that surface ever grows past a stream. **2026-08-30 scan update:** Regobs and Varsom have been consolidated into a single **Varsom** app (an Ionic 6 rewrite) combining observations, warnings and map layers in one place; its most recent update (28 May 2026) added **slope-angle and slope-angle-with-runout map layers** for Norway and Svalbard — a fourth independent example feeding [Feature inspiration](#feature-inspiration) item 6. |
| **LAWIS** | AT | Open database of snow profiles, avalanche events and observations across the Alps. Potentially a *source* as much as a competitor. |
| **Bergfex** | AT | The default DACH tour-and-weather portal, with enormous reach and avalanche info attached. The incumbent our Austrian users likely already have installed. |
| **Outdooractive / alpenvereinaktiv** | DE / AT | Tour portals with alpine-club backing; alpenvereinaktiv carries institutional trust in AT/DE comparable to SLF's in CH. Licenses Skitourenguru's ATHM terrain-hazard methodology as a map layer (see the Skitourenguru row above). |
| **Camptocamp / Skitour.fr** | FR (community) | The French route corpora Whympr sources from. Relevant as data sources and as where French tourers actually plan. |
| **FATMAP** | Strava | 3D terrain mapping, since folded into Strava. Worth a look mainly as a post-mortem: what happened to the standalone product, and what Strava kept. Confirmed shut down for good — retired 1 Oct 2024, no 2026 development. |
| **Avalanche Canada / avalanche.org** | CA / US | North American equivalents of our providers, both with their own apps. Not competitors in our geography, but the clearest available answer to "how does someone else present a normalised multi-centre bulletin?" **2026-08-30 scan:** Avalanche Canada's rebuilt mobile app is in beta, full release expected fall 2026, adding place-based search and simplified photo-first trip reports — worth a look as a peer forecast agency's own app modernisation. |
| **Météo-France app** | Météo-France | The official front end for our third provider — the BRA as its author presents it. |
| **Windy / Meteoblue / snow-forecast.com** | various | Weather-only, no avalanche layer. They matter as the thing users check *alongside* us, which makes them integration candidates more than rivals. |
| **Brand safety apps** (Ortovox, Mammut) | manufacturers | Marketing-led companions to hardware. Low threat, occasionally good ideas — check whether any are still maintained before spending time. |
| **Aerostacks** | Slovak student startup — new find, 2026-08-30 scan | Not a bulletin app — early-stage AI/satellite/drone snowpack-stability prediction tech ("Snowdrift") aimed at institutions and ski centres, using GPR/LiDAR/multispectral sensors to model avalanche risk without manual pit tests. Upstream of the providers we ingest rather than a competitor to us; noted as a watch item only. |

---

## Feature inspiration

Candidates raised by scanning the four products above, ranked by fit
rather than by how impressive they are. "Have today" is measured against
the code in this repo as of 2026-08-19 — none of these are scoped tickets
yet.

### Strong fit — the infrastructure is already here

**1. Danger-change and new-bulletin push alerts** *(SnowSafe, OpenSnow)*
SnowSafe's most recent release put push notifications on danger levels,
and OpenSnow's whole retention model is threshold alerts — it is the one
feature a focused bulletin app and a commercial forecast platform
independently agree is essential. We have the entire stack idle: VAPID
keys wired through Render, a `PushSubscription` model, a service worker,
and `favourites` to say *which* regions a user cares about — but no
product-facing alert; `/_push-demo/` is still the only sender. The
interesting design question is the trigger, not the transport. "Danger
rose to 4" and "a new bulletin was issued for a region you saved" are
different promises, and the second is the one that survives a quiet week.
OpenSnow's lesson is to let the user set the threshold rather than
guessing it. See [`push-notifications.md`](push-notifications.md) and
[`accounts.md`](accounts.md).
**2026-08-19 scan update — this has now shipped, from a new entrant:**
AvalancheClarity (see [Not yet profiled](#not-yet-profiled)) already sends
a push notification the instant a new bulletin or revision publishes for a
subscribed region, on top of a free translate-and-explain layer over the
Météo-France bulletin. This was our best-argued idle-infrastructure
advantage; it no longer differentiates us from every competitor, only from
the three profiled above. Worth re-scoping soon rather than treating this
as a someday item. **2026-08-23 scan update:** and per the fuller
AvalancheClarity picture found this pass, that alerting layer now sits on
top of coverage spanning all three of our providers (CH, FR, AT/IT — see
[Not yet profiled](#not-yet-profiled)), not just Météo-France. The
idle-infrastructure advantage was already gone; the coverage gap it sat
inside is now narrowing too.

**2. Route ↔ bulletin coupling** *(WhiteRisk's crux analysis; Whympr, inverted)*
WhiteRisk already analyses a drawn route and flags its critical cruxes
against terrain and avalanche information — so this is a proven pattern,
not a speculative one, and we would not be first. What we would be is
**first across providers**: WhiteRisk does it inside Switzerland, and
Whympr has the route corpus but only a pass-through bulletin. We hold the
piece neither combination has — a normalised CAAML render model that
knows each problem's aspects and elevation band for CH, FR, AT and IT
alike — and, since SNOW-685/686, a `Route` model holding an uploaded GPX.
"Your route crosses N–NE between 2200 m and 2800 m, which is exactly
where today's persistent weak layer sits" is a sentence only a
bulletin-first product can write, in a geography where nobody writes it
yet. Note the corollary: don't try to build Whympr's 100,000-route
library, and do read WhiteRisk's crux presentation carefully before
designing ours.
**2026-08-19 scan update:** the France-only piece of this idea is now live
twice over — Yéti (Petzl Foundation-backed) does route-vs-bulletin risk
colour-coding using the Météo-France bulletin today, and WhiteRisk's 2026
redesign both ingested the Météo-France bulletin *and* sharpened its own
crux display with route elevation profiles. The cross-provider angle (CH +
FR + AT + IT in one render model) is still unclaimed by anyone we've
found, but the France-only version of this feature is no longer a gap —
it's a contested feature with two live implementations.

**3. A written daily read** *(OpenSnow's Daily Snow)*
OpenSnow's spine is a named meteorologist writing a regional narrative
every morning; people subscribe to the voice. We already generate
summary fields and headlines (`apps/public/headlines.py`,
[`site-structure.md`](site-structure.md)) but they are per-bulletin
furniture, not a thing anyone would come back for. A short daily
per-country read — what changed since yesterday, which problem is driving
it, where the interesting swing is — sits directly on data we already
hold, in the one place we have an advantage over every app here: we can
see all three providers at once and say what is happening *across* the
Alps. It is also the natural body for an email that isn't a
notification.

**4. Recent field observations on the bulletin page** *(Whympr's activity wall)*
Whympr surfaces the last ten days of community outings with conditions;
it is what makes a static forecast feel live. We already collect
`FieldObservation` and list it at `/observations/`, but the bulletin page
for a region doesn't show the observations filed *in* that region. Same
data, one join, much better place for it.
**2026-08-30 scan update:** Whympr's field reports are free to post
without login or subscription and auto-expire after 90 days unless
corroborated by another user — a concrete staleness/trust mechanic worth
copying alongside the join, so an unconfirmed report doesn't sit on a
bulletin page indefinitely.

**5. Resort comparison view** *(OpenSnow)*
OpenSnow lets a user compare several mountains side by side; it is one of
the most-praised parts of the product. We have `Resort`, `Favourite` and
per-region danger ratings already — a compact "your saved places today"
table with danger, trend and weather is a rendering job over existing
querysets, and it gives the favourites feature a reason to exist beyond
navigation.

### Worth scoping — real gaps, real cost

**6. Slope-angle overlay** *(WhiteRisk, Whympr)*
The single most-cited terrain layer in ski touring; WhiteRisk ships both
slope angle *and* an avalanche-terrain layer, Whympr ships slope gradient
across Europe, and we have neither. It is the most conspicuous absence on
our map. The cost is a tile source rather than app code — a slope raster
derived from a DEM, served from the existing self-hosted origin (see
[`runbooks/self-hosted-tiles.md`](runbooks/self-hosted-tiles.md)) — plus
a careful editorial line, because a 30°+ shading layer invites exactly
the "the map said it was fine" misreading our disclaimer exists to
prevent.
**2026-08-23 scan update:** onX Backcountry (US, no Alps coverage — see
[Not yet profiled](#not-yet-profiled)) is a third independent example of
this pattern, and its **safe-zone rating** — a distinct classification for
terrain with no avalanche exposure regardless of conditions, layered on
top of its ATES terrain-exposure scale — is a more careful answer to our
own "map said it was fine" worry than a bare slope-angle wash: it frames
the layer as *ruling terrain in as safe*, not as rating everything else
dangerous. Worth reading before designing our editorial line.

**7. Weather-station observations** *(WhiteRisk, SnowSafe)*
We render *forecast* weather from Open-Meteo; neither our map nor our
bulletin page carries an *observed* snowpack signal — new snow in 24 h,
snow depth, wind at ridge height — which is what a user actually checks
before committing. WhiteRisk gives station readings away free, and
SnowSafe made a station layer its headline v6.3 feature, which tells you
how load-bearing it is. SLF's IMIS network and the Austrian LWD stations
both publish. A new provider integration in `apps/weather/`, so not
small, but it fits an app we already own.

**8. Aspect/elevation rose on the bulletin page** *(SnowSafe's aspect compass)*
SnowSafe pairs aspect with the day's danger patterns in one dial. We
already resolve `danger_patterns` (DP1–DP10) for ALBINA in the render
model and render them as cards, and every provider gives aspect and
elevation per problem — so this is a presentation change over data we
hold, not an ingest change. The cheapest item on this list.
**2026-08-30 scan update:** Skida (see [Not yet profiled](#not-yet-profiled))
ships the same idea as a "pizza diagram" — aspect as compass slices,
elevation on a mountain-icon axis — a second live implementation
independent of SnowSafe. Worth a look alongside SnowSafe's dial before
designing ours; two competitors converging on the same shape is a signal,
not just an option.

**9. Snow and danger history** *(OpenSnow's 10-day lookback)*
OpenSnow sells 10 days of past snowfall and snow line per mountain. We
have the danger half of this already — `RegionDayRating` and the season
calendar ([`calendar.md`](calendar.md)) — but not the weather half, and
the two are far more useful together: a danger rating means something
different after 40 cm than after a dry fortnight. Cheap once (7) lands.

**10. National topo basemaps** *(WhiteRisk, Whympr)*
IGN and SwissTopo alongside our current basemaps — WhiteRisk licenses CH,
FR and AT topo maps, Whympr eleven of them. Straightforward against our
existing basemap picker and download machinery; the blocker is licensing,
not engineering, and the payoff is credibility with exactly the French
and Swiss users our strongest providers serve.

**11. Structured explainer content** *(WhiteRisk EXPLORE/LEARN)*
WhiteRisk's knowledge base and lesson-plus-quiz format is the deepest
educational content in the category, and it is a large part of why the
platform is trusted. We have `/how-to-read-a-bulletin/` and `/help/` —
good, but flat. The realistic version is not an e-learning module; it is
contextual: link the specific problem type or danger pattern on a
bulletin page through to a short explainer of *that* thing, so the
teaching happens where the question arises.

### Not for us

**12. Clinometer / on-site analyser / AR peak viewer** *(WhiteRisk, SnowSafe, Whympr)*
Device-sensor tools needing native APIs that calibrate badly in a
browser. WhiteRisk's Analyser is the serious version of this and it is
backed by the institute that authors the bulletin — a decision-support
tool carries a duty of care we are not positioned to take on, and our
liability disclaimer says so. Wrong product for us.

**13. GPS tracking, logbook, activity feed** *(Whympr)*
Strava's and Whympr's ground, defended by network effects and years of
recorded history. Nothing about a bulletin product gets better by adding
it.

**14. Webcams** *(Whympr)*
Genuinely useful, genuinely cheap to embed, and genuinely a distraction:
it makes us a worse version of a planning app rather than a better
bulletin. Revisit only if it turns out users open the map to check
conditions rather than to read the forecast.

**15. Professional / instructor tooling** *(WhiteRisk PRO)*
Presentation decks and student licences serve avalanche educators, a
market SLF already owns with material it authored. No route in for us.
