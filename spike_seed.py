"""
spike_seed.py — SPIKE ONLY. Ingest the sentinel bulletins onto one day.

Throwaway support script for the layer-architecture spike (PR #505). Delete
it along with the rest of the SPIKE ONLY code; it is not part of the app.

Why it exists: ``seed_test_data`` gives every micro-region its own bulletin,
all at ``moderate``. That renders the choropleth as a single flat blob, which
cannot show whether bulletin-level grouping reads as distinct blocks.

This ingests the nine canonical payloads in ``tests/sentinels/`` — real CAAML
captured from SLF, ALBINA and Météo-France — through the real
``upsert_bulletin`` path, re-dated onto one target day. That gives genuinely
irregular multi-region groupings (the SLF sentinels cover 93, 65 and 13
regions) and real danger structures (elevation-band splits, split days,
multi-problem) rather than anything invented here.

The SLF sentinels overlap. Their ``valid_from`` is staggered so the
arbitration in ``recompute_region_day`` is deterministic: the narrowest
bulletin publishes last and therefore wins its regions, the widest publishes
first and keeps whatever is left. That mirrors how a real day resolves and
deliberately exercises the claimed-set vs won-set distinction.

Requires the AT/IT/FR region fixtures for the ALBINA and Météo-France
sentinels to find their regions::

    uv run python manage.py loaddata eaws_CH eaws_AT eaws_IT eaws_FR resorts

DESTRUCTIVE: deletes every Bulletin whose target day is ``TARGET`` before
ingesting. Cascades to RegionBulletin, RegionDayRating and BulletinGrouping.
Run it against a scratch/worktree database, never one holding bulletins you
fetched from a provider and want to keep.

Usage::

    uv run python manage.py shell < spike_seed.py
"""

# ruff: noqa: T201 — this runs as a stdin script under `manage.py shell`, not as
# app code. It has no BaseCommand.stdout to write to, and routing progress
# through `logging` would hide it behind the shell's log config. `print` is the
# only sensible output channel here.

import copy
import json
import pathlib
from datetime import UTC, date, datetime, timedelta

from bulletins.models import Bulletin, BulletinGrouping, PipelineRun, RegionDayRating
from bulletins.services.day_rating import _target_day
from bulletins.services.slf_fetcher import upsert_bulletin

# Mid-winter, so the map reads like a real season day rather than a spring
# off-season one. Nothing depends on the exact value.
TARGET = date(2026, 2, 10)

SENTINELS = pathlib.Path("tests/sentinels")

# (path, minutes past 07:00Z). Later publication wins an overlapping region,
# so the narrowest SLF bulletin is listed last and takes its regions from the
# wider ones — the same way a real day's partition resolves.
ORDER = [
    ("slf/A-single-level", 0),
    ("slf/B-subdivision-plus", 10),
    ("slf/C-split-day-multi-problem", 20),
    ("albina/A-single-level", 0),
    ("albina/B-elevation-band-split", 10),
    ("albina/C-split-day-multi-problem", 20),
    ("meteofrance/A-single-level", 0),
    ("meteofrance/B-elevation-band-split", 10),
    ("meteofrance/C-elevation-split-multi-problem", 20),
]


def _iso(dt: datetime) -> str:
    """Return a CAAML-style UTC timestamp string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# 1. Drop every existing bulletin whose target day is TARGET. The SQL
#    pre-filter spans {TARGET, TARGET-1} to catch both the morning-of and
#    prior-evening issues; _target_day then decides which actually target it.
doomed = [
    b.pk
    for b in Bulletin.objects.filter(
        valid_from__date__in=[TARGET, TARGET - timedelta(days=1)]
    )
    if _target_day(b) == TARGET
]
print(f"deleting {len(doomed)} existing bulletins targeting {TARGET}")
Bulletin.objects.filter(pk__in=doomed).delete()

# 2. Re-date each sentinel onto TARGET and ingest it through the real path.
run = PipelineRun.objects.create(status=PipelineRun.Status.RUNNING)
ingested: list[str] = []

for slug, offset_minutes in ORDER:
    source = SENTINELS / slug / "source.json"
    if not source.exists():
        print(f"  SKIP {slug} — no source.json")
        continue

    raw = copy.deepcopy(json.loads(source.read_text()))

    # Morning issue: valid_from hour < 12 means _target_day == TARGET.
    start = datetime(TARGET.year, TARGET.month, TARGET.day, 7, 0, tzinfo=UTC)
    start += timedelta(minutes=offset_minutes)
    end = datetime(TARGET.year, TARGET.month, TARGET.day, 16, 0, tzinfo=UTC)

    raw["validTime"] = {"startTime": _iso(start), "endTime": _iso(end)}
    raw["publicationTime"] = _iso(start - timedelta(minutes=10))
    raw["nextUpdate"] = _iso(end)
    raw["bulletinID"] = f"spike-{slug.replace('/', '-')}-{TARGET.isoformat()}"

    upsert_bulletin(raw, run)
    ingested.append(raw["bulletinID"])
    print(f"  ingested {slug}")

run.status = PipelineRun.Status.SUCCESS
run.save(update_fields=["status"])

# 3. Report the partition, and only now — counting won regions inside the
#    ingest loop reads every bulletin before the later ones have taken their
#    overlapping regions, which makes every bulletin look like it won
#    everything it claimed.
#
#    claimed vs won is the distinction the spike is about: BulletinGrouping
#    dissolves the set a bulletin *claims*, while the colour follows the set
#    it *won* via RegionDayRating.source_bulletin.
print(f"\n{'bulletin':46s} {'claimed':>7s} {'won':>5s}")
for bulletin_id in ingested:
    bulletin = Bulletin.objects.get(bulletin_id=bulletin_id)
    claimed = bulletin.regions.count()
    won = RegionDayRating.objects.filter(source_bulletin=bulletin, date=TARGET).count()
    note = (
        "  <-- boundary would enclose regions it did not colour"
        if won < claimed
        else ""
    )
    print(f"  {bulletin_id[6:50]:44s} {claimed:7d} {won:5d}{note}")

rated = RegionDayRating.objects.filter(date=TARGET)
print(f"\nregions rated on {TARGET}: {rated.count()}")
print(
    f"distinct winning bulletins: {rated.values('source_bulletin').distinct().count()}"
)
print(
    f"BulletinGrouping rows:      {BulletinGrouping.objects.for_date(TARGET).count()}"
)
