"""
apps/data_feed/services/apply.py — Writing one feed item into this database.

The client half of the data feed (SNOW-1197). One function per feed, each
taking one wire item and returning what it did (or, without ``commit``,
would do) as an ``Outcome``. ``pull_production_data`` tallies the outcomes.

**Idempotent by construction.** Every item is matched on its natural key
and compared with the row already here; an identical row is ``UNCHANGED``
and is not written, so a second pull of the same window writes nothing.

**Each feed goes through the code path that owns its table** rather than
around it:

* a bulletin is replayed through ``upsert_bulletin`` with the provider's
  CAAML, so its region links, day ratings, grouping outline and render
  model are rebuilt by *this* environment's code — on staging, that is
  the code under test, not production's output copied in;
* weather goes through ``upsert_weather``, so the immutability rule holds:
  a past day already recorded here is kept, never rewritten (``KEPT``);
* locations and resorts are curated reference data with no ingest service,
  so they are written field by field — the same columns production holds.

Nothing is deleted. A resort or location removed on production stays here
until ``bin/sync-staging-data`` resets the estate.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.bulletins.models import Bulletin, PipelineRun
from apps.bulletins.services.slf_fetcher import upsert_bulletin
from apps.data_feed import contract
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from apps.weather.models import Weather
from apps.weather.services.upsert import upsert_weather

logger = logging.getLogger(__name__)


class Outcome(enum.StrEnum):
    """What applying one item did, or would do without ``commit``.

    The values are the tally names the command records.
    """

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    # A past weather day already recorded here that differs from the feed:
    # kept as it stands, because a day's account is never rewritten.
    KEPT = "kept"
    FAILED = "failed"


class ApplyError(ValueError):
    """An item that cannot be applied here, e.g. naming a region we lack."""


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def apply_location(item: dict[str, Any], *, commit: bool) -> Outcome:
    """Create or update a curated location by uuid, and its region centroids.

    Args:
        item: A ``locations`` feed item.
        commit: Write; otherwise only decide.

    Returns:
        The outcome.

    """
    fields = contract.load_fields(Location, contract.LOCATION_FIELDS, item["fields"])
    location = Location.objects.filter(uuid=item["uuid"]).first()
    centroid_of = item.get("centroid_of", [])
    regions = MicroRegion.objects.filter(region_id__in=centroid_of)

    if location is None:
        if commit:
            with transaction.atomic():
                location = Location.objects.create(uuid=item["uuid"], **fields)
                regions.update(centroid_location=location)
        return Outcome.CREATED

    stale_regions = regions.exclude(centroid_location=location)
    # centroid_of is the whole list on production, so a region here that
    # still points at this location but is missing from it was unlinked there.
    dropped_regions = MicroRegion.objects.filter(centroid_location=location).exclude(
        region_id__in=centroid_of
    )
    changed = {k: v for k, v in fields.items() if getattr(location, k) != v}
    if not changed and not stale_regions.exists() and not dropped_regions.exists():
        return Outcome.UNCHANGED
    if commit:
        with transaction.atomic():
            for name, value in changed.items():
                setattr(location, name, value)
            if changed:
                location.save()
            dropped_regions.update(centroid_location=None)
            stale_regions.update(centroid_location=location)
    return Outcome.UPDATED


# ---------------------------------------------------------------------------
# Resorts
# ---------------------------------------------------------------------------


def _resort_state(resort: Resort) -> dict[str, Any]:
    """Return the relations of ``resort`` in the feed's natural-key shape.

    Args:
        resort: A saved resort.

    Returns:
        ``region``, ``location``, ``passes`` and ``locations``, comparable
        with the same members of a feed item.

    """
    return {
        "region": resort.region.region_id,
        "location": str(resort.location.uuid) if resort.location else None,
        "passes": sorted(resort.passes.values_list("slug", "name", "website")),
        "locations": sorted(
            (str(uuid), role, primary)
            for uuid, role, primary in resort.resort_locations.values_list(
                "location__uuid", "role", "is_primary"
            )
        ),
    }


def _wanted_state(item: dict[str, Any]) -> dict[str, Any]:
    """Return a feed item's relations in the shape ``_resort_state`` returns.

    Args:
        item: A ``resorts`` feed item.

    Returns:
        The item's relations, normalised for comparison.

    """
    return {
        "region": item["region"],
        "location": item.get("location"),
        "passes": sorted(
            (p["slug"], p["name"], p.get("website", "")) for p in item.get("passes", [])
        ),
        "locations": sorted(
            (link["location"], link["role"], link["is_primary"])
            for link in item.get("locations", [])
        ),
    }


def _locations_by_uuid(uuids: set[str]) -> dict[str, Location]:
    """Return the locations named by ``uuids``, or raise naming any missing.

    Args:
        uuids: Location uuids an item references.

    Returns:
        ``{uuid: Location}``.

    Raises:
        ApplyError: A uuid has no location here — the ``locations`` feed
            has not been applied yet.

    """
    found = {str(loc.uuid): loc for loc in Location.objects.filter(uuid__in=uuids)}
    missing = uuids - found.keys()
    if missing:
        raise ApplyError(
            f"unknown location(s) {sorted(missing)} — pull locations first"
        )
    return found


def _resolve_resort(
    item: dict[str, Any],
) -> tuple[MicroRegion, dict[str, Location]]:
    """Return the region and locations a resort item names, or raise.

    Run before deciding an outcome, so a dry run reports an item that would
    fail rather than counting it as a create.

    Args:
        item: A ``resorts`` feed item.

    Returns:
        The region, and ``{uuid: Location}`` for the pin and every link.

    Raises:
        ApplyError: The item names a region or a location this database
            does not have.

    """
    region = MicroRegion.objects.filter(region_id=item["region"]).only("pk").first()
    if region is None:
        raise ApplyError(f"unknown region {item['region']!r}")
    wanted = {link["location"] for link in item.get("locations", [])}
    if item.get("location"):
        wanted.add(item["location"])
    return region, _locations_by_uuid(wanted)


def _write_resort(
    resort: Resort,
    item: dict[str, Any],
    fields: dict[str, Any],
    region: MicroRegion,
    by_uuid: dict[str, Location],
) -> None:
    """Write ``fields`` and the item's relations onto ``resort``.

    Args:
        resort: The resort, saved or new.
        item: The ``resorts`` feed item.
        fields: Its scalar columns, already loaded.
        region: The item's region, resolved.
        by_uuid: The item's locations, resolved.

    """
    for name, value in fields.items():
        setattr(resort, name, value)
    resort.region = region
    # The pin is copied as the row production points at, not re-placed:
    # set_resort_pin moves a location in place, which is the right rule for
    # an editor and the wrong one for a copy of a decision already made.
    resort.location = by_uuid[item["location"]] if item.get("location") else None
    resort.slug = item["slug"]
    resort.save()

    passes = []
    for entry in item.get("passes", []):
        # update_or_create, so a renamed pass or a corrected website on
        # production reaches every resort here that holds the pass.
        pass_, _ = Pass.objects.update_or_create(
            slug=entry["slug"],
            defaults={"name": entry["name"], "website": entry.get("website", "")},
        )
        passes.append(pass_)
    resort.passes.set(passes)

    resort.resort_locations.all().delete()
    ResortLocation.objects.bulk_create(
        ResortLocation(
            resort=resort,
            location=by_uuid[link["location"]],
            role=link["role"],
            is_primary=link["is_primary"],
        )
        for link in item.get("locations", [])
    )


def apply_resort(item: dict[str, Any], *, commit: bool) -> Outcome:
    """Create or update a resort by slug, with its pin, passes and links.

    Args:
        item: A ``resorts`` feed item.
        commit: Write; otherwise only decide.

    Returns:
        The outcome.

    Raises:
        ApplyError: The item names a region or location this database does
            not have.

    """
    fields = contract.load_fields(Resort, contract.RESORT_FIELDS, item["fields"])
    region, by_uuid = _resolve_resort(item)
    resort = Resort.objects.filter(slug=item["slug"]).first()
    if resort is None:
        if commit:
            with transaction.atomic():
                _write_resort(Resort(), item, fields, region, by_uuid)
        return Outcome.CREATED

    unchanged = all(getattr(resort, k) == v for k, v in fields.items())
    if unchanged and _resort_state(resort) == _wanted_state(item):
        return Outcome.UNCHANGED
    if commit:
        with transaction.atomic():
            _write_resort(resort, item, fields, region, by_uuid)
    return Outcome.UPDATED


# ---------------------------------------------------------------------------
# Bulletins
# ---------------------------------------------------------------------------


def apply_bulletin(
    item: dict[str, Any], *, commit: bool, run: PipelineRun | None
) -> Outcome:
    """Replay a bulletin's CAAML through this database's ``upsert_bulletin``.

    Unchanged when the stored CAAML and PDF link are identical, which is
    what makes a re-pull of the same window a no-op: ``upsert_bulletin``
    would otherwise rewrite the row and recompute its derived tables.

    Args:
        item: A ``bulletins`` feed item.
        commit: Write; otherwise only decide.
        run: The ``PipelineRun`` to attribute a written bulletin to;
            required when ``commit``.

    Returns:
        The outcome.

    Raises:
        ApplyError: The item carries no CAAML.

    """
    caaml = item.get("caaml")
    if not isinstance(caaml, dict):
        raise ApplyError(f"bulletin {item.get('bulletin_id')!r} carries no CAAML")
    pdf_url = item.get("pdf_url") or ""
    existing = (
        Bulletin.objects.filter(bulletin_id=item["bulletin_id"])
        .only("pk", "raw_data", "pdf_url")
        .first()
    )
    if existing is not None:
        stored = (existing.raw_data or {}).get("properties")
        if stored == caaml and existing.pdf_url == pdf_url:
            return Outcome.UNCHANGED
    if commit:
        if run is None:
            raise ValueError("apply_bulletin needs a PipelineRun to commit")
        upsert_bulletin(caaml, run, pdf_url=pdf_url)
    return Outcome.CREATED if existing is None else Outcome.UPDATED


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


def apply_weather(item: dict[str, Any], *, commit: bool) -> Outcome:
    """Record one location-day of weather through ``upsert_weather``.

    A past day already recorded here is ``KEPT`` when it differs — the
    model's rule is that a day's account stands once the day is over, and a
    copy is no exception — and ``UNCHANGED`` when it does not.

    Args:
        item: A ``weather`` feed item.
        commit: Write; otherwise only decide.

    Returns:
        The outcome.

    Raises:
        ApplyError: The location is not here (pull locations first), or the
            day does not parse.

    """
    location = Location.objects.filter(uuid=item["location"]).first()
    if location is None:
        raise ApplyError(
            f"unknown location {item['location']!r} — pull locations first"
        )
    observed_on = parse_date(item["observed_on"])
    if observed_on is None:
        raise ApplyError(f"observed_on {item['observed_on']!r} is not a date")
    fields = contract.load_fields(Weather, contract.WEATHER_FIELDS, item["fields"])

    existing = Weather.objects.filter(
        location=location, observed_on=observed_on
    ).first()
    if existing is not None:
        if all(getattr(existing, k) == v for k, v in fields.items()):
            return Outcome.UNCHANGED
        if observed_on < timezone.localdate():
            return Outcome.KEPT
    if commit:
        upsert_weather(location, observed_on, **fields)
    return Outcome.CREATED if existing is None else Outcome.UPDATED
