---
name: weather-icons-are-drawn-in-house
description: Why Snowdesk draws its own weather icons — bin/build-weather-icons, the both-backgrounds palette, the baked silhouette edge
status: current
last-reviewed: 2026-09-01
---

# The weather icons are ours, generated from one vocabulary

**Decision.** The twelve condition buckets in `static/icons/weather/snowdesk/`
are drawn by [`bin/build-weather-icons`](../../bin/build-weather-icons) and are
what `WEATHER_ICON_SET` defaults to. Yr and Meteocons stay in the tree as
comparison sets; MeteoSwiss and the BBC stay gitignored under `_local/`.

Supersedes
[`weather-icons-are-yr-not-meteoswiss`](weather-icons-are-yr-not-meteoswiss.md),
whose licensing analysis still holds and is why no better-measuring set was
ever available to take. What changed is the conclusion: if the set we wanted
could not be licensed, the answer was to draw it.

## Why

**A vendored set cannot be chosen against our own backgrounds.** Snowdesk
renders icons on a white card, a dark card, and a light basemap. Meteocons'
cloud measures 1.16:1 on the white card and Yr's 1.36:1 — both are drawn for
one background and dissolve on the other. Compensating for that meant a CSS
filter and a matching canvas pass, and `drop-shadow(0 0 1px …)` is a blur: it
bought an edge and cost sharpness everywhere it landed, turning a six-armed
flake into a blob at 27 px.

Drawing our own removes the compensation rather than tuning it. The palette
is chosen against **both** card backgrounds, and the silhouette edge is a
fixed mid grey painted into the artwork — so there is no `.weather-icon`
class, no `--color-weather-icon-halo`, and no canvas filter for this set.

**Generated, not hand-drawn.** Every shape comes from one vocabulary — a
cloud, a sun, a moon, drops, flakes, fog bars — so fourteen files cannot
drift apart, and retuning is a constant rather than fourteen edits. The
script rewrites the set byte-for-byte, so the committed files are always
reproducible from it.

## Consequences

- **`bin/build-weather-icons` is the source of truth.** Editing an SVG in
  `snowdesk/` by hand is a change the next run silently discards.
- **The cloud ramp carries intensity**, six steps from a bright fair-weather
  cumulus to a near-black cumulonimbus, anchored on Yr's own four neutral
  greys. `partly_cloudy` and `cloudy` share a step deliberately: what
  separates them is the sun, not the shade, and the two greys that had told
  them apart were 1.06 contrast — a distinction the eye cannot make standing
  in for one it can.
- **The edge is what makes the ramp possible.** The lightest fill fails on
  the white card and the darkest fails on the dark card; a fixed mid grey
  (3.45 / 4.26) carries whichever end the fill cannot, and disappears into
  the fills that need no help. Remove it and both ends of the ramp break.
- **The comparison machinery stays — minus the strip (amended SNOW-842).**
  `/_icon-sets/`, the `?icons=<name>` override and the other sets are kept
  because this is a decision that gets revisited, and a comparison nobody
  can reproduce is not evidence. `SETS_NEEDING_HALO` still exists for the
  sets that do need the filter.

  The floating DEBUG switcher strip this clause originally also protected
  was removed. Keeping a comparison reproducible does not require it to be
  on screen: the strip rendered over the content of every page on every
  local load, and the two things that make the comparison reproducible —
  the override and the side-by-side grid — are both reached deliberately.
  The strip was the one part that could not be turned off.
- **Sunrise and sunset are still Meteocons.** No set publishes an
  equivalent, and they are not condition icons.

Measured at 27 px on white, against the sets it was chosen over: mean ink
29.4 to Yr's 23.1 and Meteocons' 7.9, with MeteoSwiss — the unlicensable
best — at 35.1. Method and the full table:
[`weather-icons-are-yr-not-meteoswiss`](weather-icons-are-yr-not-meteoswiss.md).

See [`weather-surfaces.md`](../weather-surfaces.md) for the buckets, the
day/night rule and the four surfaces that read them.
