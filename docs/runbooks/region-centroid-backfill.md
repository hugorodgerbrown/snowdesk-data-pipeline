---
name: region-centroid-backfill
description: link_region_centroid_locations --commit — give every micro-region a centroid Location; Open-Meteo cost and the fetch_weather bill
status: current
last-reviewed: 2026-08-30
---

# Runbook — give every micro-region a centroid Location

## When this applies

After SNOW-759 deploys to an environment. The migration adds the `Weather`
table, but **no region gets weather until it has a `centroid_location`**,
and that FK is filled by a `--commit`-gated command an operator runs by
hand, per
[`dry-run-default-commands`](../decisions/dry-run-default-commands.md).

Until it is run, an environment degrades rather than breaking:
`MicroRegion.centre_point()` falls back to the legacy `centre` column, so
the bulletin page's JSON-LD geo and the MCP nearby-region search keep
working. What is missing is weather — a region with no centroid `Location`
is not in `Location.objects.active()` and so is never fetched.

## Read this before you run it

**This step costs money twice, and the second cost is recurring.**

| | Calls | When |
|---|---|---|
| The backfill itself | up to **461** Open-Meteo elevation calls — AT 153, CH 149, IT 124, FR 35 | once |
| The fetch it enables | ~**1,800** additional forecast calls **per day** (461 locations × 4 runs) | for ever |

The recurring figure is the one that matters. **Confirm the Open-Meteo plan
has headroom for it before running this in production.** The free tier is
10,000 calls/day shared per IP; 1,800 is a large fraction of it on top of
the existing resort estate.

The command paces itself with `--delay` (default 1.0s) to stay inside the
free-tier rate limit, so a full run takes roughly eight minutes.

## Steps

### 1. Preview

Read-only. It still makes every elevation call — that is what proves a
region can resolve — but writes nothing.

```bash
uv run python manage.py link_region_centroid_locations
```

Expect `N region(s) would be linked, 0 skipped, 0 failed`. A non-zero
`skipped` is a region whose `centre` column holds something unreadable,
which is a fixture problem to fix rather than a reason to stop. A non-zero
`failed` exits non-zero — check the logs before continuing.

### 2. Commit

```bash
uv run python manage.py link_region_centroid_locations --commit
```

Idempotent: regions that already have a `centroid_location` are excluded
from the candidate set, so a second run selects zero and costs nothing. A
partial run that died halfway can simply be re-run.

### 3. Confirm the estate grew as expected

```bash
uv run python manage.py shell -c "
from apps.locations.models import Location
from apps.regions.models import MicroRegion
print('linked  :', MicroRegion.objects.filter(centroid_location__isnull=False).count())
print('unlinked:', MicroRegion.objects.filter(centroid_location__isnull=True).count())
print('active  :', Location.objects.active().count())
"
```

The `active` count is the number of Open-Meteo calls each `fetch_weather`
run will now make. Multiply by four for the daily bill.

### 4. Fetch once by hand before the scheduler does

```bash
uv run python manage.py fetch_weather            # read-only probe
uv run python manage.py fetch_weather --commit
```

Running it by hand once surfaces a rate-limit rejection or a bad
coordinate while someone is watching, rather than at 00:00 UTC in the
scheduler's log.

## Rolling back

There is no un-link command, and adding one would be the wrong shape: the
centroid `Location` rows are real places in the estate and other things may
already reference them. To stop the cost without unpicking the data, take
the `fetch_weather` job out of `schedule.py` and redeploy — the rows stay,
they simply stop being refreshed.

## Progress log

| Environment | Date | Regions linked | Active locations after |
|---|---|---|---|
| _(record production and staging runs here)_ | | | |
