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

**Reads are batched per page** (SNOW-1199). ``lookups_for`` reads every
row a page of items refers to in one query per kind, and each apply
function reads from that ``PageLookups`` rather than querying per item.
Writes stay per item. A write that changes a looked-up row (a region's
centroid) updates the cached row too, so later items on the page see it.

**A dry run reports what a commit would do** (SNOW-1198). A location a dry
run counts as created goes into ``PageLookups.planned``, a set the command
shares across the run's feeds, and the weather and resort items after it
treat that location as present instead of failing.

Nothing is deleted. A resort or location removed on production stays here
until ``bin/sync-staging-data`` resets the estate.
"""

from __future__ import annotations

import datetime as dt
import enum
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Prefetch, Q
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
# Page lookups
# ---------------------------------------------------------------------------


@dataclass
class PageLookups:
    """The rows a page of feed items refers to, read once for the whole page.

    Attributes:
        locations: Locations here, by uuid string.
        regions: Micro-regions by ``region_id``: those a page names, and,
            for the locations feed, those centred on the page's locations.
        resorts: Resorts here by slug, with region, pin, passes and links
            loaded.
        bulletins: Bulletins here by ``bulletin_id`` (pk, CAAML and PDF
            link only).
        weather: Weather rows here by ``(location pk, observed_on)``.
        planned: Location uuids a dry run has counted as created. The
            command shares one set across a run's feeds.

    """

    locations: dict[str, Location] = field(default_factory=dict)
    regions: dict[str, MicroRegion] = field(default_factory=dict)
    resorts: dict[str, Resort] = field(default_factory=dict)
    bulletins: dict[str, Bulletin] = field(default_factory=dict)
    weather: dict[tuple[int, dt.date], Weather] = field(default_factory=dict)
    planned: set[str] = field(default_factory=set)


def _is_uuid(value: object) -> bool:
    """Return whether ``value`` is a string a uuid column can be queried with."""
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _locations(uuids: Iterable[object]) -> dict[str, Location]:
    """Return the locations here among ``uuids``, by uuid string.

    A value that is not a uuid is left out rather than queried, which would
    raise for the whole page: its item then fails on its own, as an
    unknown location.
    """
    wanted = {str(value) for value in uuids if _is_uuid(value)}
    return {
        str(location.uuid): location
        for location in Location.objects.filter(uuid__in=wanted)
    }


def _regions(query: Q) -> dict[str, MicroRegion]:
    """Return the micro-regions matching ``query``, by ``region_id``.

    Only the columns the apply functions read, so a centroid is compared by
    its id and never loaded.
    """
    return {
        region.region_id: region
        for region in MicroRegion.objects.filter(query).only(
            "pk", "region_id", "centroid_location_id"
        )
    }


def lookups_for(
    feed: str, items: list[dict[str, Any]], *, planned: set[str] | None = None
) -> PageLookups:
    """Read every row ``items`` refers to, in one query per kind of row.

    Args:
        feed: The feed the items came from.
        items: One page of that feed's items.
        planned: The run's set of locations a dry run would create; a new
            set when not given.

    Returns:
        The page's lookups.

    """
    lookups = PageLookups(planned=set() if planned is None else planned)
    if feed == "locations":
        lookups.locations = _locations(item["uuid"] for item in items)
        named = {rid for item in items for rid in item.get("centroid_of", [])}
        lookups.regions = _regions(
            Q(region_id__in=named)
            | Q(centroid_location__in=list(lookups.locations.values()))
        )
    elif feed == "resorts":
        lookups.locations = _locations(
            uuid
            for item in items
            for uuid in ([item["location"]] if item.get("location") else [])
            + [link["location"] for link in item.get("locations", [])]
        )
        lookups.regions = _regions(Q(region_id__in={item["region"] for item in items}))
        lookups.resorts = {
            str(resort.slug): resort
            for resort in Resort.objects.filter(
                slug__in={item["slug"] for item in items}
            )
            .select_related("region", "location")
            .prefetch_related(
                "passes",
                Prefetch(
                    "resort_locations",
                    queryset=ResortLocation.objects.select_related("location"),
                ),
            )
        }
    elif feed == "bulletins":
        lookups.bulletins = {
            bulletin.bulletin_id: bulletin
            for bulletin in Bulletin.objects.filter(
                bulletin_id__in={item["bulletin_id"] for item in items}
            ).only("pk", "bulletin_id", "raw_data", "pdf_url")
        }
    elif feed == "weather":
        lookups.locations = _locations(item.get("location") for item in items)
        lookups.weather = _weather_days(items, lookups.locations)
    return lookups


def _parse_day(value: object) -> dt.date | None:
    """Return ``value`` as a date, or None when it is not an ISO date string."""
    if not isinstance(value, str):
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def _weather_days(
    items: list[dict[str, Any]], locations: dict[str, Location]
) -> dict[tuple[int, dt.date], Weather]:
    """Return the weather rows here for exactly the page's (location, day) pairs.

    One clause per location with that location's own days, so a sparse
    page never loads the cross product of every location and every day.
    An item whose location or day does not resolve is left out; it fails
    on its own when applied.
    """
    days_by_location: dict[int, set[dt.date]] = {}
    for item in items:
        location = locations.get(str(item.get("location")))
        day = _parse_day(item.get("observed_on"))
        if location is not None and day is not None:
            days_by_location.setdefault(location.pk, set()).add(day)
    if not days_by_location:
        return {}
    query = Q()
    for location_pk, days in days_by_location.items():
        query |= Q(location_id=location_pk, observed_on__in=days)
    return {
        (row.location_id, row.observed_on): row for row in Weather.objects.filter(query)
    }


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def _recentre(regions: list[MicroRegion], location_pk: int | None) -> None:
    """Point the cached ``regions`` at ``location_pk``, as a commit leaves them.

    Keeps a page's lookups true after a write (or a dry run's decision), so
    a later item on the page does not drop a region this one has just
    claimed.
    """
    for region in regions:
        region.centroid_location_id = location_pk


def _create_location(
    item: dict[str, Any],
    fields: dict[str, Any],
    claimed: list[MicroRegion],
    *,
    commit: bool,
    lookups: PageLookups,
) -> None:
    """Create a location new here, or record that a dry run would (SNOW-1198).

    Args:
        item: The ``locations`` feed item.
        fields: Its columns, already loaded.
        claimed: The regions here it is the centroid of.
        commit: Write; otherwise only plan.
        lookups: The page's lookups, updated to match.

    """
    if not commit:
        lookups.planned.add(item["uuid"])
        return
    with transaction.atomic():
        location = Location.objects.create(uuid=item["uuid"], **fields)
        MicroRegion.objects.filter(pk__in=[r.pk for r in claimed]).update(
            centroid_location=location
        )
    lookups.locations[item["uuid"]] = location
    _recentre(claimed, location.pk)


def apply_location(
    item: dict[str, Any], *, commit: bool, lookups: PageLookups | None = None
) -> Outcome:
    """Create or update a curated location by uuid, and its region centroids.

    Args:
        item: A ``locations`` feed item.
        commit: Write; otherwise only decide.
        lookups: The page's lookups; read for this one item when not given.

    Returns:
        The outcome.

    """
    lookups = lookups or lookups_for("locations", [item])
    fields = contract.load_fields(Location, contract.LOCATION_FIELDS, item["fields"])
    location = lookups.locations.get(item["uuid"])
    centroid_of = set(item.get("centroid_of", []))
    claimed = [r for rid, r in lookups.regions.items() if rid in centroid_of]

    if location is None:
        _create_location(item, fields, claimed, commit=commit, lookups=lookups)
        return Outcome.CREATED

    stale = [r for r in claimed if r.centroid_location_id != location.pk]
    # centroid_of is the whole list on production, so a region here that
    # still points at this location but is missing from it was unlinked there.
    dropped = [
        r
        for rid, r in lookups.regions.items()
        if r.centroid_location_id == location.pk and rid not in centroid_of
    ]
    changed = {k: v for k, v in fields.items() if getattr(location, k) != v}
    if not changed and not stale and not dropped:
        return Outcome.UNCHANGED
    if commit:
        with transaction.atomic():
            for name, value in changed.items():
                setattr(location, name, value)
            if changed:
                location.save()
            MicroRegion.objects.filter(pk__in=[r.pk for r in dropped]).update(
                centroid_location=None
            )
            MicroRegion.objects.filter(pk__in=[r.pk for r in stale]).update(
                centroid_location=location
            )
    _recentre(dropped, None)
    _recentre(stale, location.pk)
    return Outcome.UPDATED


# ---------------------------------------------------------------------------
# Resorts
# ---------------------------------------------------------------------------


def _resort_state(resort: Resort) -> dict[str, Any]:
    """Return the relations of ``resort`` in the feed's natural-key shape.

    Reads the relations ``lookups_for`` loaded, so it costs no queries.

    Args:
        resort: A saved resort, with region, pin, passes and links loaded.

    Returns:
        ``region``, ``location``, ``passes`` and ``locations``, comparable
        with the same members of a feed item.

    """
    return {
        "region": resort.region.region_id,
        "location": str(resort.location.uuid) if resort.location else None,
        "passes": sorted((p.slug, p.name, p.website) for p in resort.passes.all()),
        "locations": sorted(
            (str(link.location.uuid), link.role, link.is_primary)
            for link in resort.resort_locations.all()
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


def _resolve_resort(
    item: dict[str, Any], lookups: PageLookups
) -> tuple[MicroRegion, dict[str, Location]]:
    """Return the region and locations a resort item names, or raise.

    Run before deciding an outcome, so a dry run reports an item that would
    fail rather than counting it as a create. A location the dry run has
    planned counts as present; it is not in the returned map, which only a
    commit reads, and on a commit nothing is planned.

    Args:
        item: A ``resorts`` feed item.
        lookups: The page's lookups.

    Returns:
        The region, and ``{uuid: Location}`` for the pin and every link.

    Raises:
        ApplyError: The item names a region or a location this database
            does not have.

    """
    region = lookups.regions.get(item["region"])
    if region is None:
        raise ApplyError(f"unknown region {item['region']!r}")
    wanted = {link["location"] for link in item.get("locations", [])}
    if item.get("location"):
        wanted.add(item["location"])
    missing = wanted - lookups.locations.keys() - lookups.planned
    if missing:
        raise ApplyError(
            f"unknown location(s) {sorted(missing)} — pull locations first"
        )
    return region, {u: lookups.locations[u] for u in wanted if u in lookups.locations}


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


def apply_resort(
    item: dict[str, Any], *, commit: bool, lookups: PageLookups | None = None
) -> Outcome:
    """Create or update a resort by slug, with its pin, passes and links.

    Args:
        item: A ``resorts`` feed item.
        commit: Write; otherwise only decide.
        lookups: The page's lookups; read for this one item when not given.

    Returns:
        The outcome.

    Raises:
        ApplyError: The item names a region or location this database does
            not have.

    """
    lookups = lookups or lookups_for("resorts", [item])
    fields = contract.load_fields(Resort, contract.RESORT_FIELDS, item["fields"])
    region, by_uuid = _resolve_resort(item, lookups)
    resort = lookups.resorts.get(item["slug"])
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
    item: dict[str, Any],
    *,
    commit: bool,
    run: PipelineRun | None,
    lookups: PageLookups | None = None,
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
        lookups: The page's lookups; read for this one item when not given.

    Returns:
        The outcome.

    Raises:
        ApplyError: The item carries no CAAML.

    """
    caaml = item.get("caaml")
    if not isinstance(caaml, dict):
        raise ApplyError(f"bulletin {item.get('bulletin_id')!r} carries no CAAML")
    pdf_url = item.get("pdf_url") or ""
    lookups = lookups or lookups_for("bulletins", [item])
    existing = lookups.bulletins.get(item["bulletin_id"])
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


def apply_weather(
    item: dict[str, Any], *, commit: bool, lookups: PageLookups | None = None
) -> Outcome:
    """Record one location-day of weather through ``upsert_weather``.

    A past day already recorded here is ``KEPT`` when it differs — the
    model's rule is that a day's account stands once the day is over, and a
    copy is no exception — and ``UNCHANGED`` when it does not. A location a
    dry run has planned has no weather here yet, so its day is ``CREATED``.

    Args:
        item: A ``weather`` feed item.
        commit: Write; otherwise only decide.
        lookups: The page's lookups; read for this one item when not given.

    Returns:
        The outcome.

    Raises:
        ApplyError: The location is not here (pull locations first), or the
            day does not parse.

    """
    lookups = lookups or lookups_for("weather", [item])
    location = lookups.locations.get(item["location"])
    if location is None and item["location"] not in lookups.planned:
        raise ApplyError(
            f"unknown location {item['location']!r} — pull locations first"
        )
    observed_on = parse_date(item["observed_on"])
    if observed_on is None:
        raise ApplyError(f"observed_on {item['observed_on']!r} is not a date")
    fields = contract.load_fields(Weather, contract.WEATHER_FIELDS, item["fields"])
    if location is None:
        return Outcome.CREATED

    existing = lookups.weather.get((location.pk, observed_on))
    if existing is not None:
        if all(getattr(existing, k) == v for k, v in fields.items()):
            return Outcome.UNCHANGED
        if observed_on < timezone.localdate():
            return Outcome.KEPT
    if commit:
        upsert_weather(location, observed_on, **fields)
    return Outcome.CREATED if existing is None else Outcome.UPDATED
