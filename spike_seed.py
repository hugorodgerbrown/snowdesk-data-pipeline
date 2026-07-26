"""
spike_seed.py — SPIKE ONLY. Build a realistic bulletin partition for one day.

Throwaway support script for the layer-architecture spike (PR #505). Delete
it along with the rest of the SPIKE ONLY code; it is not part of the app.

Why it exists: ``seed_test_data`` gives every micro-region its own bulletin,
all at ``moderate``. That renders the choropleth as a single flat blob, which
cannot show whether bulletin-level grouping reads as distinct blocks. This
replaces one day's bulletins with 21 multi-region ones — one per SubRegion,
which is roughly how SLF actually groups terrain — with ratings cycling the
full EAWS scale so neighbouring groups contrast.

DESTRUCTIVE: deletes every Bulletin whose target day is ``TARGET`` before
rebuilding. Cascades to RegionBulletin, RegionDayRating and BulletinGrouping.
Run it against a scratch/worktree database, never one holding bulletins you
fetched from a provider and want to keep.

Usage::

    uv run python manage.py shell < spike_seed.py
"""

# ruff: noqa: T201 — this runs as a stdin script under `manage.py shell`, not as
# app code. It has no BaseCommand.stdout to write to, and routing progress
# through `logging` would hide it behind the shell's log config. `print` is the
# only sensible output channel here.

from datetime import date, timedelta

from bulletins.management.commands.seed_test_data import _make_bulletin_params
from bulletins.models import Bulletin, BulletinGrouping, RegionBulletin
from bulletins.services.day_rating import _target_day, apply_bulletin_day_ratings
from bulletins.services.grouping import compute_bulletin_grouping_boundary
from bulletins.services.render_model import RENDER_MODEL_VERSION, build_render_model
from regions.models import SubRegion

# The map reference date the worktree seed already centres on.
TARGET = date(2026, 4, 8)

# Cycled between neighbouring sub-regions so adjacent bulletin groups are
# visually distinguishable rather than all landing on the same colour.
SCALE = ["low", "moderate", "considerable", "high", "very_high"]

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

# 2. One bulletin per sub-region, covering all of its micro-regions.
subs = list(SubRegion.objects.prefetch_related("micro_regions").order_by("prefix"))
print(f"building {len(subs)} sub-region bulletins")

for idx, sub in enumerate(subs):
    regions = list(sub.micro_regions.all())
    if not regions:
        continue
    danger = SCALE[idx % len(SCALE)]
    params = _make_bulletin_params(
        region_id=sub.prefix,
        region_name=sub.name_en or sub.name_native,
        target_date=TARGET,
        danger_value=danger,
    )
    # Distinct id per sub-region so we never collide with the deleted set.
    params["bulletin_id"] = f"spike-{sub.prefix}-{TARGET.isoformat()}"
    bulletin = Bulletin.objects.create(**params)

    bulletin.render_model = build_render_model(bulletin.raw_data["properties"])
    bulletin.render_model_version = RENDER_MODEL_VERSION
    bulletin.save(update_fields=["render_model", "render_model_version"])

    RegionBulletin.objects.bulk_create(
        [RegionBulletin(bulletin=bulletin, region=r) for r in regions]
    )

    # The real ingest path, so the spike exercises the same code production
    # would: day ratings first, then the dissolve.
    apply_bulletin_day_ratings(bulletin)
    compute_bulletin_grouping_boundary(bulletin)
    print(f"  {sub.prefix:8s} {len(regions):3d} regions  {danger}")

print("groupings:", BulletinGrouping.objects.for_date(TARGET).count())
