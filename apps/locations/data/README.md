# Curated location sheets

Two tab-separated exports, reconciled into `Location` and `ResortLocation`
by `import_locations` (dry-run by default; `--commit` to apply):

| File | One row per | Keyed by |
|---|---|---|
| `locations.tsv` | a place | `uuid` |
| `resort_locations.tsv` | a resort-to-location link | `(resort_uuid, location_uuid)` |

`resort_name` and `location_name` in the links sheet are **informational
only** — they make the file readable and are never matched on.

## Why two sheets, and neither on the resort sheet

Flattening locations into resort columns (`top_name`, `top_lat`, `top_lon`)
would cap how many a resort can have and force Mont Fort to be repeated once
per resort — the duplication the model exists to remove. The links need their
own file rather than a multi-value cell for the same reason: a cell holding
`verbier:TOP;nendaz:TOP;…` is nested data in a flat medium and cannot be
sorted, filtered or diffed.

## There is no elevation column

Elevation is always derived, never supplied (`docs/locations.md`).
`link_location_forecast_cells` resolves it from the coordinate via
Open-Meteo, alongside the forecast cell.

**That makes the elevation a check on the coordinate.** Run the link command
after adding a row and compare the resolved height against the resort
sheet's `base_elevation_m` / `top_elevation_m`. A location whose resolved
height is nowhere near the expected figure has been mis-pinned — catch it
here rather than on the resort page.

## Coverage — deliberately partial

Four village rows, all elevation-verified. **No peaks or mid-stations yet**,
and that is the open curation work rather than an oversight.

The first attempt at this tranche pinned Mont Fort and Piz Corvatsch from
memory. The elevation check caught both: Mont Fort resolved at 2302 m
against an expected 3328 m, and Piz Corvatsch at 3204 m against 3303 m. A
local-maximum search over the elevation grid found nearby summits, but
could not confirm *which* summit — and near Corvatsch it found 3428 m, the
mountain, where the resort sheet's 3303 m is the lift-served top station.
Those are different places, and only one of them is where people ski from.

So the peaks need a curator with a gazetteer, not a plausible guess. Until
then the resort sheet's `top_elevation_m` remains the only record of them —
which is the state SNOW-701 exists to end, one verified row at a time.

Adding a peak means: pin it, run `link_location_forecast_cells` without
`--commit`, and check the resolved height against the resort sheet before
committing anything.
