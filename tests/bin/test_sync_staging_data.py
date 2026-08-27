"""
tests/bin/test_sync_staging_data.py — the staging-refresh table lists.

``bin/sync-staging-data`` clears its target tables with ``TRUNCATE`` and
**no** ``CASCADE``, which is the safety property the whole script rests on:
a list that misses a referencing table makes Postgres name it and refuse,
rather than quietly emptying something the script was never meant to touch.

That matters concretely here. ``regions_microregion.centroid_location_id``
references ``locations_location``, so a ``CASCADE`` would reach the region
table and, through it, most of the database. The column is nullable, so the
script nulls it, truncates, and rebuilds it with
``link_region_centroid_locations``.

The cost of no-CASCADE is that the list has to be the exact closure of
tables referencing the copied set — and that closure is a property of the
model graph, which moves. A new model with a foreign key into a copied
table breaks the script at 07:20 UTC in a cron job, days after the migration
that caused it. These tests read the lists out of the script and recompute
the closure from Django's own metadata, so the break happens here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "sync-staging-data"

# The one reference into the copied set from a table that must survive.
# Nullable, so the script releases it rather than truncating through it.
NULLED_REFERENCE = ("regions_microregion", "centroid_location_id")


def _bash_array(name: str) -> list[str]:
    """Return the entries of a bash array literal in the script.

    Args:
        name: The array's variable name.

    Returns:
        The table names it lists, comments and blanks stripped.

    """
    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{name}=\(\n(.*?)\n\)$", source, re.MULTILINE | re.DOTALL)
    assert match, f"{name} array not found in {SCRIPT.name}"
    return [
        line.strip()
        for line in match.group(1).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@pytest.fixture(scope="module")
def declared() -> set[str]:
    """Every table the script clears — loaded and clear-only alike."""
    return set(_bash_array("LOAD_ORDER")) | set(_bash_array("CLEAR_ONLY"))


def _inbound_foreign_keys() -> dict[str, list[tuple[str, str, bool]]]:
    """Map each table to the (table, column, nullable) triples pointing at it."""
    inbound: dict[str, list[tuple[str, str, bool]]] = {}
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model is not None:
                inbound.setdefault(field.related_model._meta.db_table, []).append(
                    (model._meta.db_table, str(field.column), bool(field.null))
                )
    return inbound


def test_truncate_list_is_the_complete_closure(declared: set[str]) -> None:
    """Nothing outside the list references anything inside it.

    If this fails, ``TRUNCATE`` without ``CASCADE`` aborts the nightly run.
    The fix is to add the named table to ``CLEAR_ONLY`` — or, if it must
    survive and its column is nullable, to null it first the way
    ``centroid_location_id`` is handled.
    """
    inbound = _inbound_foreign_keys()
    unlisted = [
        f"{source}.{column} -> {target}"
        for target in declared
        for source, column, _ in inbound.get(target, [])
        if source not in declared and (source, column) != NULLED_REFERENCE
    ]

    assert not unlisted, (
        "These tables reference tables bin/sync-staging-data truncates, but are "
        f"not in its lists, so TRUNCATE will refuse: {unlisted}"
    )


def test_the_nulled_reference_is_still_nullable() -> None:
    """The escape hatch only works while that column stays nullable.

    Made NOT NULL by some later migration, the script would have to truncate
    ``regions_microregion`` instead — which it must never do, and which the
    closure test above would not catch on its own.
    """
    table, column = NULLED_REFERENCE
    model = next(m for m in apps.get_models() if m._meta.db_table == table)
    field = next(f for f in model._meta.concrete_fields if str(f.column) == column)

    assert field.null, (
        f"{table}.{column} is no longer nullable; bin/sync-staging-data "
        "releases it before truncating and can no longer do so."
    )


def test_every_listed_table_exists(declared: set[str]) -> None:
    """A renamed or dropped table would fail the run, not be skipped."""
    known = {model._meta.db_table for model in apps.get_models()}
    assert declared <= known, f"unknown tables listed: {sorted(declared - known)}"


def test_load_order_puts_parents_before_children() -> None:
    """A table is loaded after every table it points at.

    Each ``COPY`` runs in its own transaction, so Django's deferred foreign
    keys are checked per table and load order is load-bearing rather than
    cosmetic.
    """
    order = _bash_array("LOAD_ORDER")
    position = {table: index for index, table in enumerate(order)}

    for model in apps.get_models():
        table = model._meta.db_table
        if table not in position:
            continue
        for field in model._meta.concrete_fields:
            if not field.is_relation or field.related_model is None:
                continue
            parent = field.related_model._meta.db_table
            if parent in position and parent != table:
                assert position[parent] < position[table], (
                    f"{table} is loaded before its parent {parent} (via {field.column})"
                )


def test_no_user_table_is_loaded() -> None:
    """The script copies nothing personal into staging.

    ``CLEAR_ONLY`` deliberately empties staging's own favourites,
    observations and shares — they reference the copied set — but nothing
    refills them, and no user-owned table is ever loaded from production.
    """
    forbidden = {
        "auth_user",
        "accounts_account",
        "accounts_subscription",
        "accounts_passkeycredential",
        "accounts_pushsubscription",
        "core_requestlog",
        "routes_route",
        "favourites_favourite",
        "observations_fieldobservation",
        "bulletins_bulletinshare",
        "bulletins_bulletinshareclick",
        "django_session",
    }

    assert not forbidden & set(_bash_array("LOAD_ORDER"))
