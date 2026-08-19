---
name: competitors
description: Competitor list — WhiteRisk, SnowSafe, Whympr, OpenSnow — with feature profiles and the feature ideas each one suggests for Snowdesk
status: current
last-reviewed: 2026-08-19
---

# Competitor list

**Last automated competitor scan: 2026-08-19.** News, product-update and
new-entrant findings from that pass are marked inline as "2026-08-19 scan
update"; the full source list is in the PR description for that change.

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
  — re-verify by direct fetch next pass).
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
- **Note:** WhiteRisk is also our explicit design model — see "Why we're
  replicating WhiteRisk" in [`design-system.md`](design-system.md).
  Treating it as both benchmark and competitor is deliberate: we replicate
  its page structure, then subtract.

## SnowSafe

- **What it is:** a focused avalanche-bulletin app (iOS/Android) by First
  Line Solutions GmbH. Explicitly *not* a planner: "all avalanche
  information at your fingertips."
- **Checked:** 2026-08-19 — <https://snowsafe.at/>, App Store listing
  (v6.3.0810, released Aug 2026).
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
  reliable to operate as a transceiver* — is the same instinct behind our
  replicate-then-subtract plan.

## Whympr

- **What it is:** an all-in-one mountain trip-planning app (iOS/Android +
  web), born in Chamonix. Hiking, ski touring, mountaineering, climbing,
  trail running, snowshoeing.
- **Checked:** 2026-08-19 — <https://get.whympr.com/en>, App Store listing
  (v2.38.1, 4.4★). **Re-scanned 2026-08-19** (routine competitor scan) via
  web search — <https://get.whympr.com/en/blog-articles/trail-difficulty-ratings-for-every-hiking-path-all-on-one-map>
  (direct fetch blocked by this session's network policy).
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
  no funding, team or acquisition news found this pass.
- **Overlap with Snowdesk:** adjacent rather than direct. Its avalanche
  forecasts cover the US and Canada only, so it does not compete in the
  Alps today — but its *shape* is the most commercially proven in this
  list, and the parts worth copying are editorial, not geographic.
- **Where it is stronger:**
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
| **AvalancheClarity** | Simon Perry (guidebook author, Tignes) — new find, 2026-08-19 scan | **New entrant, profile this one first, ahead of Skitourenguru.** A free app + web widget that AI-translates and plain-language-explains the official Météo-France bulletin, works offline, and — per PlanetSKI and SnowBrains coverage — sends a push notification the moment a new bulletin or a revision is published for a subscribed region. That is [Feature inspiration](#feature-inspiration) item 1 (danger-change/new-bulletin alerts), the idea we flagged as our strongest available differentiator, already shipped by someone else. On 30 March 2026 it also launched an embeddable widget (one line of code, accepts GPS coordinates, region codes or a multi-point touring route) that any resort, guide or chalet site can drop in — a distribution channel we don't have an answer to. No account required; free. Direct fetch of avalancheclarity.com and the SnowBrains writeup was blocked by this session's network policy, so this entry is search-corroborated across independent sources (PlanetSKI, SnowBrains, a snowheads.com forum thread, a stylealtitude.com writeup, the Google Play listing) but not yet primary-verified — verify by direct fetch before treating any figure here as exact. |
| **Skitourenguru & Yéti** | Günter Schmudlach (CH) / Petzl Foundation-backed (FR) | The most directly threatening idea in the category: both rate individual ski tours by avalanche risk **daily**, by combining the official bulletin with terrain analysis over a route database. This is [Feature inspiration](#feature-inspiration) item 2 as a shipped product — now shipped twice. **2026-08-19 scan update:** Petzl Foundation funding has made both **free to all users** (previously gated); the combined route database is now ~13,000 tours across the Alps (~4,600 in the French Alps alone), with ~500 more planned for the Pyrenees during 2026, and a Petzl-produced tutorial video series is rolling out explaining how bulletin + terrain data combine. Yéti is the newer, France-only sibling product (Météo-France bulletin + DTM terrain analysis) — worth its own row once profiled, currently bundled here since the two are developed and funded together. Profile Skitourenguru first as originally planned, but note Yéti alongside it. |
| **avalanche.report** | EUREGIO / ALBINA | The provider's own web front end — one of our three upstreams. Sets the expectation for how an ALBINA bulletin "should" look, and its region map is the thing our AT/IT users already know. 2026-08-19 scan found no material product changes since last review — only a backend admin-GUI fix, not user-facing. |
| **Regobs / Varsom** | NVE (NO) | State-run community observation platform at national scale. The reference design for `FieldObservation` if that surface ever grows past a stream. |
| **LAWIS** | AT | Open database of snow profiles, avalanche events and observations across the Alps. Potentially a *source* as much as a competitor. |
| **Bergfex** | AT | The default DACH tour-and-weather portal, with enormous reach and avalanche info attached. The incumbent our Austrian users likely already have installed. |
| **Outdooractive / alpenvereinaktiv** | DE / AT | Tour portals with alpine-club backing; alpenvereinaktiv carries institutional trust in AT/DE comparable to SLF's in CH. |
| **Camptocamp / Skitour.fr** | FR (community) | The French route corpora Whympr sources from. Relevant as data sources and as where French tourers actually plan. |
| **FATMAP** | Strava | 3D terrain mapping, since folded into Strava. Worth a look mainly as a post-mortem: what happened to the standalone product, and what Strava kept. |
| **Avalanche Canada / avalanche.org** | CA / US | North American equivalents of our providers, both with their own apps. Not competitors in our geography, but the clearest available answer to "how does someone else present a normalised multi-centre bulletin?" |
| **Météo-France app** | Météo-France | The official front end for our third provider — the BRA as its author presents it. |
| **Windy / Meteoblue / snow-forecast.com** | various | Weather-only, no avalanche layer. They matter as the thing users check *alongside* us, which makes them integration candidates more than rivals. |
| **Brand safety apps** (Ortovox, Mammut) | manufacturers | Marketing-led companions to hardware. Low threat, occasionally good ideas — check whether any are still maintained before spending time. |

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
as a someday item.

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
