---
name: region-centroid-backfill
description: Region centroid Locations — not re-linked on deploy; an operator runs link_region_centroid_locations and link_resort_locations --commit
status: current
last-reviewed: 2026-08-31
---

# Runbook — region centroid Locations

## The backfill is offline, and an operator runs it

This document used to describe paying ~461 Open-Meteo elevation calls per
environment. That cost is gone: SNOW-771 put the elevations in the fixture,
so `link_region_centroid_locations` is now wholly offline.

It briefly ran on every deploy, to repair what the deploy's own `loaddata`
had just wiped. Both are gone now — the deploy no longer reloads fixtures
(a bulk write that a timeout leaves half applied), so nothing wipes the
link and nothing needs to repair it. Run the command when you seed an
environment or change a fixture.

If you are here because region weather is missing in an environment, the
answer is almost certainly not "run the backfill" — see
[Diagnosing](#diagnosing-a-region-with-no-weather) below.

## Why it works this way

`build.sh` used to reload the four EAWS fixtures on every deploy.
`loaddata` builds each instance from the fixture's fields alone and saves
the whole row, so any column the fixtures do not carry is reset to its
model default. `MicroRegion.centroid_location` is one of those, so **every
deploy NULLed all 461 links** and orphaned the `Location` rows behind them.

That was a silent data-loss bug for as long as the link was treated as
durable: `link_region_centroid_locations` would report "461 linked, 0
failed", and hours later a deploy would undo it with nothing in any log to
say so. Staging lost 461 links this way on 2026-08-30, and the symptom was
a weather map empty for every region.

The fix is not to defend the FK but to make rebuilding it free:

| Half of a centroid | Where it comes from | Needs network? |
|---|---|---|
| Coordinate | `centre_from_bbox(boundary)` — the boundary is in the fixture | No |
| Elevation | `MicroRegion.centroid_elevation_m` — also in the fixture | No |

So the link step costs nothing but a query and is safe to re-run at any
time. It reuses the existing anonymous `Location` at each coordinate rather
than minting a new one, so re-running never orphans weather.

## The one manual step — and it is not per environment

`centroid_elevation_m` has to be resolved once, against the **committed
fixtures**, by a developer. Every environment then gets it for free.

Run it after adding regions to a fixture, or after a fixture rebuild moves
a boundary:

```bash
# Preview.
uv run python manage.py refresh_centroid_elevations

# Resolve the missing ones and write the fixtures.
uv run python manage.py refresh_centroid_elevations --commit

# After a rebuild moved boundaries — re-resolve every entry, since a moved
# centroid leaves a stale elevation behind and nothing else would notice.
uv run python manage.py refresh_centroid_elevations --commit --force
```

Commit the changed fixtures.
`tests/regions/management/commands/test_link_region_centroid_locations.py`
fails if any region with a boundary is left without an elevation, so a
half-finished run cannot ship.

## What still costs money

Nothing here does, any more. The **recurring** cost is `fetch_weather`, and
it is unchanged by this document: one Open-Meteo forecast call per active
location, four times a day. Giving all 461 micro-regions a centroid takes
that to roughly **1,800 additional calls per day**, for ever.

**Confirm the Open-Meteo plan has headroom before a deploy first puts
centroids into an environment.** The free tier is 10,000 calls/day shared
per IP. Staging is on the paid tier (`customer-api.open-meteo.com`), so it
is not constrained; production's `OPEN_METEO_API_BASE_URL` is set
independently and must be checked on its own.

## Diagnosing a region with no weather

Work down this list; the first miss is the cause.

```bash
uv run --no-sync python manage.py shell -c "
from apps.locations.models import Location
from apps.regions.models import MicroRegion
from apps.weather.models import Weather
print('regions            :', MicroRegion.objects.count())
print('regions w/ centroid:', MicroRegion.objects.filter(centroid_location__isnull=False).count())
print('public locations   :', Location.objects.public().count())
print('weather rows       :', Weather.objects.count())
"
```

- **`regions w/ centroid` is 0** — the environment has not been linked.
  Run `link_region_centroid_locations --commit`; it is safe and offline.
- **Linked, but `public locations` is small** — `public()` is
  `resort_locations OR micro_regions`; a centroid reaches it only through
  the FK above.
- **Public, but no `weather rows`** — `fetch_weather` has not run for those
  locations yet. It is scheduled 4×/day; run it by hand to fill today.

## Rolling back

There is no un-link command, and adding one would be the wrong shape: the
centroid `Location` rows are real places in the estate and other things may
reference them. To stop the recurring cost without unpicking data, take the
`fetch_weather` job out of `schedule.py` and redeploy — the rows stay, they
simply stop being refreshed.

## History

| Date | Environment | What happened |
|---|---|---|
| 2026-08-24 | Staging | Linked 461 by hand. Undone by a later deploy; not understood at the time. |
| 2026-08-30 | Staging | Linked 461 by hand again, fetched 470 weather rows, then a deploy NULLed every link. Diagnosed as SNOW-771. |
| 2026-08-30 | Local | 149 / 149 linked from the CH fixture, offline. |
