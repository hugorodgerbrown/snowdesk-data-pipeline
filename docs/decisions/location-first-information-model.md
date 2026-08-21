---
name: location-first-information-model
description: Historical — Map → Region → Location tiers and weather sourcing; NamedLocation split withdrawn, superseded by location-is-the-primitive
status: historical
last-reviewed: 2026-08-21
---

# Location-first information model

**Superseded by [`location-is-the-primitive`](location-is-the-primitive.md).**
Sections 4 and 5 below — `NamedLocation` as a curated table beside an
anonymous `ForecastPoint`, and identity opt-in — invert the model: they make
a place point at a weather row, when the weather is *for* the place. Their
stated justification, that an anonymous cell serves a public resort and a
private pin "without leaking either", is not a property of this system —
`Favourite` stores the exact pin, and
[`forecast-point-quantisation`](forecast-point-quantisation.md) gives the
cell's rationale as fetch cost and forecast resolution, never privacy.

Sections 1–3 — the three tiers, the weather-sourcing split, and *a location
is a point; a resort has several* — are correct and are carried forward into
the superseding file.

---

**Original decision, accepted 2026-08-21.** The tier boundaries were the product call this
needed, and they are now settled: Location is the working surface, Region is
the indexed one, and the bulletin document sits beside them as provenance.

This file still carries three decisions in one — the information model, the
weather-sourcing rule, and the relevance primitive — and should be split into
three files, since each constrains different code. That split is deferred, not
dropped; it is one document here because the three arrived as one argument.

The four open questions at the foot remain open. None of them blocks the
model, and each is scoped to a surface rather than to the tiers themselves.

## Decision

### 1. Three tiers, with the bulletin document beside them rather than above

| Tier | What it is | Owns |
|---|---|---|
| **Map** | The multi-region overview. Already the homepage. | Choosing where you care about |
| **Region** | The public, indexable, cacheable tier. | Context — neighbouring regions, observations, resorts, season history |
| **Location** | The primary working surface: a resort, a saved pin, a route, or a live position. | Point weather and the bulletin resolved to *here* |

**Bulletin-as-document** — "exactly what the provider published today, across
all its regions" — is a *provenance* surface reached laterally from Region.
It is not a tier above Region. Nobody drills down from a published document;
they check it to confirm we have not distorted it.

The bulletin page stops being the region page. Region becomes level two, and
users are expected to work at Location, zooming out for context rather than
starting there.

### 2. Weather: numbers from points, decoration from snapshots

`ForecastPointWeather` is the canonical source for any value a user might act
on — temperature, snowfall, wind, freezing level, hourly detail — on every
surface, today and forecast alike. `WeatherSnapshot` keeps two jobs it is
uniquely suited to: driving the day/night bucket *visual*, and cheap
region-scale history, which the point record structurally cannot provide
because it is forecast-only.

Neither path pins a weather model. Both take Open-Meteo's default, which
already selects the highest-resolution model per location (see SNOW-699).

Today is the hero; the forecast is secondary. Every surface leads with day 0
and offers the remaining days behind a strip or a click.

### 3. A location is a point; a resort has several

A **location is a point** — `(lat, lon)`. Elevation is a *lookup* on it, not an
independent attribute; `resolve_forecast_point` already works exactly this way.

The things users care about are **sets of locations**. A favourite is a set of
one. A route is a set of many (`Route.points`). A resort is an area, and should
be represented by several named points — village, mid-station, top — not one.

**Elevation extent** — the input to bulletin relevance — is a property of the
*set*, not of any location in it. For a resort it is the curated
`base_elevation_m`/`top_elevation_m` pair; for a route it is the profile's
range. A pin is a one-element set where the extent collapses, which is why it
looked like a degenerate range: it is a one-element set, not a special case.

So weather is **point-shaped** (one lat/lon per forecast) and bulletin
relevance is **extent-shaped** (a band, because the bulletin is banded). These
are two questions, not one primitive.

### 4. `NamedLocation` — curated public places, shared between referents

```
NamedLocation
  name            "Mont Fort" / "Verbier village"
  latitude, longitude
  kind            VILLAGE | MID | PEAK
  forecast_point  → weather.ForecastPoint (PROTECT, shared)

Resort ↔ NamedLocation   many-to-many, role per relationship
```

Mont Fort is **one row** referenced by Verbier, Nendaz, Veysonnaz and Thyon.
The fixture already encodes this as the scalar `3330` copied into four
unrelated rows; Corvatsch appears three times as `3303`. The sharing is real
data currently stored as a coincidence.

### 5. The weather anchor is universal; identity is opt-in

`ForecastPoint` is the universal anchor — deliberately anonymous and quantised,
which is what lets one cell serve a public resort and a private pin without
leaking either. `NamedLocation` is its opposite: named, curated, public, stable.

**Neither `Favourite` nor `FieldObservation` gains a required `NamedLocation`
FK.** A favourite is an arbitrary user pin, and minting a curated public row per
pin would put private placements in a public table — `favourite_detail` is
`sharing=False` / `private, no-store` precisely because a pin's name is the
user's own text. An observation is an *event at a coordinate*, not a place; two
reports at one spot a week apart must never collapse into one, and it carries a
provenance model (`latitude` vs `gps_latitude`, `location_source`,
`accuracy_radius_km`) that a place FK would erase.

`Favourite` may gain an **optional** `location` FK on the pattern of its
existing `resort` FK (SNOW-499, `SET_NULL`): favouriting Mont Fort records that
it is Mont Fort; a pin dropped 200 m away stays anonymous. Observations get
"near Mont Fort" as a nearest-`NamedLocation` *query* at display time, never a
stored relationship.

## Why

**Region was the finest scope available before the map existed.** It no longer
is. Favourites, resorts, routes and live position all give a point or a range,
and the render model already carries per-band elevation on `ratings[]` and
aspect/elevation geography on problems — so the bulletin can be resolved to a
location without any new ingest.

**The location tier already exists, in one app.**
`annotate_problem_relevance` (`apps/favourites/relevance.py`) annotates each
problem card `APPLIES` / `ABOVE` / `BELOW` against a pin's elevation. It is the
most focused surface in the product and it is locked behind sign-in and a saved
pin. The resort page holds `base_elevation_m` and `top_elevation_m` and uses
neither; routes hold `ascent_m` and `bounds` and use neither.

**Position → region resolution is already in production.** `region_for_point`
(`apps/regions/services/point_match.py`) does pure-Python point-in-polygon for
the GPS-gated field-report feature. The drill-down needs no new geo machinery.

**A resort needs several points, not a better single one.** Live elevation
lookups at the coordinates we store put Verbier's forecast point at 1436 m,
Nendaz at 1249 m and Veysonnaz at 1231 m, against terrain running to 3330 m.
That village reading is *not wrong* — it is what someone looking at a resort
wants, and every resort weather page leads with it. It is unlabelled and alone.
The fix is village **and** top, each named, not a different single anchor. The
median resort spans 1085 m of vertical, which crosses EAWS bands, so no one
point can answer "what does the bulletin say here" either.

**The weather split is already the stated rule in one place.**
`apps/favourites/views.py` documents that it "deliberately does **not** fall
back to the region-centroid `WeatherSnapshot` — that would misrepresent the
point's own conditions". SNOW-509 then gave the resort page exactly that
fallback and SNOW-571 added numbers to it, so the resort page currently shows
region-centroid values directly above point values for the same day.

## Consequences

**Region stays the indexed tier.** `BulletinSitemap` emits evergreen
`/<region_id>/<slug>/` at priority 0.8, and that is what search engines and
LLMs cite. Most Location surfaces cannot be indexed: `favourite_detail` is
deliberately `sharing=False` and `Cache-Control: private, no-store` because a
pin's name is the user's own text. Location becoming primary for *use* must not
make Region secondary for *discovery*.

**The resort page is the reference implementation.** It is the only Location
surface that is public, indexable, elevation-bearing and already exists 164
times over. It is where the model should be proven first.

**Degradation runs backwards along the drill-down.** Location depends on a
position, a point forecast and a bulletin — it is the tier most likely to fail
in the field, which is exactly where it is most wanted. Location must fall back
to Region, never to nothing. This is a design requirement, not an error path.

**Elevation acquisition is an open cost.** Saved locations get elevation once
via `resolve_forecast_point` → `fetch_elevation`. A live GPS position has only
device elevation, which is unreliable, or needs a lookup per position.

**`Resort.forecast_point` retires.** Once a resort reaches weather through its
`NamedLocation`s, the single FK from SNOW-503 is just the village location's
point restated. `ForecastPoint.active()` gains `NamedLocation` as a referent and
eventually loses `Resort` — but not at the same time. Resort-anchored points
must stay active until the resort page stops reading `Resort.forecast_point`,
so the widening (SNOW-700), SNOW-696's region referent, and the removal are
three edits in three tickets, in that order.

**Every forecast on a multi-point surface must name its elevation.** The current
resort forecast is defensible data presented without its label; adding a second
point makes labelling mandatory rather than merely better.

**`NamedLocation` needs a glossary entry** when it lands (CLAUDE.md: a domain
term gaining a code symbol goes in `docs/glossary.md`).

**Highlight, never suppress.** A problem is never hidden because it does not
apply at the user's elevation. The existing rule is load-bearing: it is the
line between helping someone read a bulletin and deciding for them, and it is
what our liability disclaimer rests on. Any range-aware version inherits it.

**Tickets this re-frames rather than replaces.** SNOW-680 (bulletin page's
lower half as the local-knowledge layer), SNOW-665 (unify Subscription and
Favourite into places), SNOW-668 (`/account/places/`) and SNOW-681
(source-bulletin prominence) each point at part of this without naming it.
SNOW-696 gives every region a `ForecastPoint`: still correct. A region has no
natural named points the way a resort does, so the multi-point model does not
extend to it — the centroid anchor is fine for the map's low-zoom decoration and
a region-level default, provided the surface says which elevation it represents.
That labelling requirement is the only change it needs.

## Open questions

1. **Does the bulletin document get a URL of its own**, or does the provenance
   link continue to point at the provider's own PDF (`docs/archive_pdfs/`)?
2. **Is a live GPS position a Location**, or is the tier limited to saved
   things — resort, favourite, route — with live position only ever selecting
   one?
3. **Does the Region page keep a danger hero**, or defer the headline number to
   Location and present itself purely as context?
4. **Which `kind`s does `NamedLocation` need at launch** — is VILLAGE / MID /
   PEAK enough, or do lift-served tops need distinguishing from summits reached
   on foot?
