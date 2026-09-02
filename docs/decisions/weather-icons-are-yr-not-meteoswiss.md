---
name: weather-icons-are-yr-not-meteoswiss
description: Why the weather condition icons are Yr / MET Norway, not MeteoSwiss, AccuWeather or Met Office — legibility measurements and licence per set
status: historical
last-reviewed: 2026-09-01
---

# The weather icons are Yr's, because they are the best set we may publish

> **Superseded by
> [`weather-icons-are-drawn-in-house`](weather-icons-are-drawn-in-house.md)
> (SNOW-791, 2026-09-01).** Snowdesk draws its own set now. Everything below
> still holds and is why: no better-measuring set was ever available to
> license, so the remaining move was to draw one. Kept for the measurements
> and the licence position on each candidate, which is the part a future
> "could we just use X?" needs.

**Decision.** The twelve condition icons in `static/icons/weather/` are
[Yr / MET Norway](https://github.com/metno/weathericons) (MIT, © 2015–2017 Yr).
They replaced Meteocons `fill` in SNOW-791. MeteoSwiss's symbols measure better
and are **not available to us**; neither are AccuWeather's or the Met Office's.

## Why

Meteocons `fill` is drawn for a dark background. On `--color-card` (`#ffffff`)
its cloud bodies measure 1.07–1.16:1 and its hairline outline 2.56:1 — every one
of those values passes on the dark card, which is the tell.

Five sets, rendered at 27 px (the map symbol size) on white, over the eight
conditions all five publish. **Ink presence** is mean darkness across the 27×27
cell, 0–255 — a blank white square scores 0. **Mark size** is the number of
pixels differing by ΔE > 10 between two conditions a reader must separate,
which at this size is what decides whether the distinguishing mark survives.

| Set | Ink presence | Mark size, overcast/fog | light/heavy snow | rain/light snow | Licence |
|---|---|---|---|---|---|
| Meteocons `fill` (was) | 7.9 | 109 px | 161 px | 43 px | MIT |
| AccuWeather | 9.8 | 215 px | 44 px | 65 px | proprietary, no public licence |
| Met Office | 15.2 | 191 px | 194 px | 210 px | Crown copyright, licensed case by case |
| **Yr / MET Norway (is)** | **23.1** | **127 px** | **295 px** | **258 px** | **MIT** |
| MeteoSwiss | 35.2 | 580 px | 385 px | 575 px | all rights reserved |

MeteoSwiss wins on every measure and cannot be used. Its
[legal terms](https://www.meteoswiss.admin.ch/about-us/legal-information.html)
make the site's content "including photos, graphics, videos, text and designs"
their sole property and transfer no rights on download; the CC BY 4.0 open-data
licence covers *meteorological and climatological data*, not the symbols. The
Met Office is Crown copyright and licenses reuse "under terms and conditions
which may include the payment of a fee" — obtainable in principle, not
take-and-go. AccuWeather publishes no licence. (`cdn.discover.swiss`'s
AccuWeather-numbered set is a third redraw, also all rights reserved.)

**No set confuses one condition for another.** Every pair above differs across
dozens to hundreds of pixels at mean ΔE 28–95. An earlier version of this
comparison reported a "worst confusable pair" from a mean taken over the whole
tile and concluded Meteocons could not tell rain from snow; that was an
artefact. A whole-tile mean measures how much ink an icon carries — twenty
strongly-differing pixels average away against seven hundred identical ones.
Presence is what actually separated these sets, and it is a whole-tile mean, so
that column is sound.

## Consequences

- **Yr is second by measurement and first by availability.** Don't re-propose
  MeteoSwiss without a licence in hand; the comparison has been run.
- **`fog.svg` is patched, not vendored clean.** Upstream's bars already differ
  from overcast across 124 px, but at mean ΔE 34 — a mark of the right size
  and the wrong weight. Ours darkens them to `#666666` and thickens them 1.35×
  at `translate(0,72)`, which leaves the size alone (127 px) and doubles the
  contrast (ΔE 68). Re-apply the edit on any re-pull from upstream. Details in
  `static/icons/weather/LICENSE.md`.
- **The set reads by silhouette, not by fill.** Yr's cloud is `#dddddd`, 1.28:1
  on white. Both the map (`decodeWeatherIcon`'s canvas drop-shadow in
  `static/js/map.js`) and the server-rendered surfaces (`.weather-icon` in
  `src/css/main.css`) dilate a dark edge along the alpha channel. Removing
  either puts the icon back on a near-white plate with nothing to meet.
- **Every file except `clearsky_*` embeds a base64 PNG** for cloud shading.
  Fine at 27–40 px including 2× DPR, but the clouds cannot be recoloured by
  editing paths.
- **Raising the ceiling means drawing our own**, in the MeteoSwiss idiom — a
  dark cloud mass, a saturated sun, countable precipitation marks, tokenised so
  they invert on the dark card. Licensing someone else's is not the route.

Scoring a future candidate: render at 27 px on white, take mean `255 − value`
for presence, and for a pair take the count of pixels differing by ΔE > 10 and
their mean ΔE. Judge the pair on both — a small number of high-ΔE pixels is a
mark that vanishes at size, and a large number of low-ΔE pixels is a mark that
blends.

See [`weather-surfaces.md`](../weather-surfaces.md) for the icon buckets, the
day/night rule and the four surfaces that read them.
