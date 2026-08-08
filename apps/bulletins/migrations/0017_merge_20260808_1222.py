# Merge migration — reunites the two leaf nodes the bulletins migration
# graph acquired on `main`, so `migrate` has a single latest migration to
# resolve to again.
#
# How the split happened: SNOW-582 (#613) added
# `0015_uppercase_pipelinerun_status` -> `0016_uppercase_bulletin_source`,
# and SNOW-571 (#609) — whose branch was cut BEFORE that landed — added
# `0015_weathersnapshot_snowfall_sum_and_more` depending on `0014` directly.
# The `main` ruleset does not require a branch to be up to date before
# merging (`strict=false`), so SNOW-571 merged on a green run that had never
# seen SNOW-582's migrations. Neither PR was wrong on its own; the graph only
# forked once both were on `main`, which is why no PR check caught it.
#
# Carries no operations of its own: both branches' operations are unrelated
# (weather-snapshot columns on one side, choice-value ALTERs on the other)
# and neither depends on the other's result, so the merge is a pure
# graph join and safe to apply in any order relative to them.
#
# Landed on the SNOW-645 branch because that branch could not go green
# without it — `makemigrations --check` fails on any fork, so every branch
# cut from this point is blocked until the graph is rejoined.

from django.db import migrations


class Migration(migrations.Migration):
    """Rejoin the forked bulletins migration graph. No schema change."""

    dependencies = [
        ("bulletins", "0015_weathersnapshot_snowfall_sum_and_more"),
        ("bulletins", "0016_uppercase_bulletin_source"),
    ]

    operations: list[migrations.operations.base.Operation] = []
