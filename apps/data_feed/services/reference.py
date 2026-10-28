"""
apps/data_feed/services/reference.py — Applying the reference-data service's v2 feeds.

The client half of ``/api/data/v2/`` (SNOW-1272), read by
``pull_reference_data``. Every place, pass and resort the service serves
is keyed by its uuid, which this instance holds as the row's
``source_uuid``, so nothing here depends on production's own ids and a
favourite on a synced resort survives every sync: user data points at
this instance's primary keys, and the apply never changes one.

  ReferenceIndex
      This instance's synced places, passes, resorts and regions, read
      once per run (a few thousand slim rows), so each item costs no
      lookup query. A write keeps the index true for the items after it.

  apply_reference_location / apply_reference_pass / apply_reference_resort
      Create or update one row by ``source_uuid``. A place's ``centroid_of``
      claims those regions' centroids; a resort's document names its
      region by code and its pin, places and passes by service uuid.

  apply_tombstone(kind, item)
      A removed place, pass or resort. A row this instance's user data
      still points at — a favourite, a trip, a field report — is detached
      rather than deleted: its ``source_uuid`` is cleared, so it leaves the
      synced estate (``reference_data()``) and every link to it is dropped,
      but the user's own row keeps its target. Anything else is deleted.
      Tombstones are applied after every live item, so a resort that moved
      off a place has already let go of it.

  reference_weather_lookups(items)
      The v2 weather feed names a place by its service uuid; this resolves
      it, so ``apply_weather`` (and its never-rewrite-a-past-day rule)
      applies the item unchanged.

  apply_reference_day_rating(item)
      ``apply_day_rating_item``: copy the service's rating, or rate the pair
      here when the rule versions differ.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from apps.bulletins.services.day_rating import apply_day_rating_item
from apps.data_feed import contract
from apps.data_feed.services.apply import (
    ApplyError,
    PageLookups,
    _weather_days,
)
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort

logger = logging.getLogger(__name__)

# What applying one item did, beyond the v1 outcomes.
CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
DELETED = "deleted"
DETACHED = "detached"
ABSENT = "absent"

# The location columns a v2 place carries: four under ``fields``, the rest
# in its ``document``.
_PLACE_FIELDS: tuple[str, ...] = ("name", "latitude", "longitude", "elevation_m")
_PLACE_DOCUMENT_FIELDS: tuple[str, ...] = (
    "short_id",
    "kind",
    "what3words",
    "what3words_fetched_at",
)
# A resort's document ``fields``: every curated column but its uuid.
_RESORT_FIELDS: tuple[str, ...] = tuple(
    name for name in contract.RESORT_FIELDS if name != "source_uuid"
)


def _uuid(value: object) -> uuid.UUID:
    """Return ``value`` as a UUID, or raise ``ApplyError``.

    Args:
        value: A wire uuid string.

    Returns:
        The UUID.

    Raises:
        ApplyError: It is not a uuid.

    """
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ApplyError(f"{value!r} is not a uuid") from exc


@dataclass
class ReferenceIndex:
    """This instance's synced rows, read once per run.

    Attributes:
        locations: Locations by ``source_uuid``.
        passes: Passes by ``source_uuid``.
        resorts: Resorts by ``source_uuid``, with their pin, passes and
            place links loaded.
        regions: Micro-regions by code (``pk``, ``region_id`` and
            ``centroid_location_id`` only).

    """

    locations: dict[uuid.UUID, Location] = field(default_factory=dict)
    passes: dict[uuid.UUID, Pass] = field(default_factory=dict)
    resorts: dict[uuid.UUID, Resort] = field(default_factory=dict)
    regions: dict[str, MicroRegion] = field(default_factory=dict)

    @classmethod
    def load(cls) -> ReferenceIndex:
        """Read the index from the database.

        Returns:
            The index.

        """
        return cls(
            locations={
                loc.source_uuid: loc
                for loc in Location.objects.reference_data()
                if loc.source_uuid is not None
            },
            passes={
                p.source_uuid: p
                for p in Pass.objects.filter(source_uuid__isnull=False)
                if p.source_uuid is not None
            },
            resorts={
                r.source_uuid: r
                for r in Resort.objects.reference_data()
                .select_related("location", "region")
                .prefetch_related("passes", "resort_locations__location")
                if r.source_uuid is not None
            },
            regions={
                region.region_id: region
                for region in MicroRegion.objects.only(
                    "pk", "region_id", "centroid_location_id"
                )
            },
        )


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


def _place_values(item: dict[str, Any]) -> dict[str, Any]:
    """Return the location columns a v2 place item sets."""
    values = contract.load_fields(Location, _PLACE_FIELDS, item.get("fields") or {})
    values.update(
        contract.load_fields(
            Location, _PLACE_DOCUMENT_FIELDS, item.get("document") or {}
        )
    )
    return values


def _set_centres(
    location: Location, claim: list[MicroRegion], release: list[MicroRegion]
) -> None:
    """Point ``claim`` at ``location`` and ``release`` at nothing, in the index too.

    Args:
        location: A saved place.
        claim: Regions whose centre it now is.
        release: Regions whose centre it no longer is.

    """
    MicroRegion.objects.filter(pk__in=[r.pk for r in release]).update(
        centroid_location=None
    )
    MicroRegion.objects.filter(pk__in=[r.pk for r in claim]).update(
        centroid_location=location
    )
    for region in release:
        region.centroid_location_id = None
    for region in claim:
        region.centroid_location_id = location.pk


def apply_reference_location(
    item: dict[str, Any], *, commit: bool, index: ReferenceIndex
) -> str:
    """Create or update one place by ``source_uuid``, and its region centres.

    A new place keeps the service's uuid as its own ``uuid`` too, as the
    curating instance does (its ``source_uuid`` is its ``uuid``). A row
    here with that ``uuid`` but no ``source_uuid`` — a v1 pull from before
    the key was sent — is adopted rather than duplicated.

    Args:
        item: A v2 ``locations`` item that is not a tombstone.
        commit: Write; otherwise only decide.
        index: The run's index, updated to match.

    Returns:
        ``created``, ``updated`` or ``unchanged``.

    """
    source = _uuid(item["uuid"])
    values = _place_values(item)
    centroid_of = set((item.get("document") or {}).get("centroid_of") or [])
    location = index.locations.get(source)
    outcome = CREATED if location is None else UPDATED
    if location is None:
        location = Location.objects.filter(uuid=source).first() or Location(uuid=source)
        location.source_uuid = source
        changed = values
    else:
        changed = {k: v for k, v in values.items() if getattr(location, k) != v}
    claim = [
        r
        for code, r in index.regions.items()
        if code in centroid_of
        and (location.pk is None or r.centroid_location_id != location.pk)
    ]
    release = [
        r
        for code, r in index.regions.items()
        if location.pk is not None
        and r.centroid_location_id == location.pk
        and code not in centroid_of
    ]
    if outcome == UPDATED and not changed and not claim and not release:
        return UNCHANGED
    if commit:
        with transaction.atomic():
            for name, value in changed.items():
                setattr(location, name, value)
            if changed or outcome == CREATED:
                location.save()
            _set_centres(location, claim, release)
        index.locations[source] = location
    return outcome


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------


def apply_reference_pass(
    item: dict[str, Any], *, commit: bool, index: ReferenceIndex
) -> str:
    """Create or update one season pass by ``source_uuid``.

    Args:
        item: A v2 ``passes`` item that is not a tombstone.
        commit: Write; otherwise only decide.
        index: The run's index, updated to match.

    Returns:
        ``created``, ``updated`` or ``unchanged``.

    """
    source = _uuid(item["uuid"])
    document = item.get("document") or {}
    values = {
        "slug": item["slug"],
        "name": document.get("name", ""),
        "website": document.get("website", ""),
    }
    pass_ = index.passes.get(source)
    if pass_ is None:
        if commit:
            # A pass a v1 pull created has the slug but no source_uuid yet:
            # adopt it rather than collide on the unique slug. Only an
            # unlinked row: one already keyed to another uuid is that
            # pass, and re-keying it would orphan its own updates.
            pass_ = (
                Pass.objects.filter(
                    slug=values["slug"], source_uuid__isnull=True
                ).first()
                or Pass()
            )
            for name, value in values.items():
                setattr(pass_, name, value)
            pass_.source_uuid = source
            pass_.save()
            index.passes[source] = pass_
        return CREATED
    if all(getattr(pass_, k) == v for k, v in values.items()):
        return UNCHANGED
    if commit:
        for name, value in values.items():
            setattr(pass_, name, value)
        pass_.save()
    return UPDATED


# ---------------------------------------------------------------------------
# Resorts
# ---------------------------------------------------------------------------


def _resort_state(resort: Resort) -> dict[str, Any]:
    """Return a saved resort's relations in the document's shape."""
    return {
        "region": resort.region.region_id,
        "location": str(resort.location.source_uuid) if resort.location else None,
        "passes": sorted(str(p.source_uuid) for p in resort.passes.all()),
        "locations": sorted(
            (str(link.location.source_uuid), link.role, link.is_primary)
            for link in resort.resort_locations.all()
        ),
    }


def _wanted_state(document: dict[str, Any]) -> dict[str, Any]:
    """Return a resort document's relations, normalised for comparison."""
    return {
        "region": document.get("region"),
        "location": document.get("location"),
        "passes": sorted(document.get("passes") or []),
        "locations": sorted(
            (link["location"], link["role"], link["is_primary"])
            for link in document.get("locations") or []
        ),
    }


def _resolve(
    document: dict[str, Any], index: ReferenceIndex, *, commit: bool
) -> tuple[MicroRegion, dict[str, Location], list[Pass]]:
    """Return the region, places and passes a resort document names, or raise.

    A dry run has written none of this run's new places or passes, so it
    checks only the region; a commit needs every one.

    Raises:
        ApplyError: Something named is not here.

    """
    region = index.regions.get(document.get("region") or "")
    if region is None:
        raise ApplyError(f"unknown region {document.get('region')!r}")
    wanted = {link["location"] for link in document.get("locations") or []}
    if document.get("location"):
        wanted.add(document["location"])
    places = {u: index.locations.get(_uuid(u)) for u in wanted}
    passes = {u: index.passes.get(_uuid(u)) for u in document.get("passes") or []}
    if commit:
        missing = sorted(
            [u for u, loc in places.items() if loc is None]
            + [u for u, p in passes.items() if p is None]
        )
        if missing:
            raise ApplyError(f"unknown place(s) or pass(es) {missing}")
    return (
        region,
        {u: loc for u, loc in places.items() if loc is not None},
        [p for p in passes.values() if p is not None],
    )


def apply_reference_resort(
    item: dict[str, Any], *, commit: bool, index: ReferenceIndex
) -> str:
    """Create or update one resort by ``source_uuid``, with its links.

    Args:
        item: A v2 ``resorts`` item that is not a tombstone.
        commit: Write; otherwise only decide.
        index: The run's index, updated to match.

    Returns:
        ``created``, ``updated`` or ``unchanged``.

    Raises:
        ApplyError: The document names a region, place or pass not here.

    """
    source = _uuid(item["uuid"])
    document = item.get("document") or {}
    fields = contract.load_fields(Resort, _RESORT_FIELDS, document.get("fields") or {})
    region, places, passes = _resolve(document, index, commit=commit)
    resort = index.resorts.get(source)
    if resort is not None:
        same = all(getattr(resort, k) == v for k, v in fields.items())
        if (
            same
            and resort.slug == item["slug"]
            and _resort_state(resort) == _wanted_state(document)
        ):
            return UNCHANGED
    outcome = CREATED if resort is None else UPDATED
    if not commit:
        return outcome
    with transaction.atomic():
        if resort is None:
            # A resort a v1 pull created has the slug but no source_uuid.
            # Only an unlinked row is adopted, as for a pass.
            resort = (
                Resort.objects.filter(
                    slug=item["slug"], source_uuid__isnull=True
                ).first()
                or Resort()
            )
        for name, value in fields.items():
            setattr(resort, name, value)
        resort.slug = item["slug"]
        resort.source_uuid = source
        resort.region = region
        resort.location = (
            places.get(document["location"]) if document.get("location") else None
        )
        resort.save()
        resort.passes.set(passes)
        resort.resort_locations.all().delete()
        ResortLocation.objects.bulk_create(
            ResortLocation(
                resort=resort,
                location=places[link["location"]],
                role=link["role"],
                is_primary=link["is_primary"],
            )
            for link in document.get("locations") or []
        )
    # Re-read with its relations, so a later comparison sees the write.
    index.resorts[source] = (
        Resort.objects.select_related("location", "region")
        .prefetch_related("passes", "resort_locations__location")
        .get(pk=resort.pk)
    )
    return outcome


# ---------------------------------------------------------------------------
# Tombstones
# ---------------------------------------------------------------------------


def _bury_pass(source: uuid.UUID, *, commit: bool, index: ReferenceIndex) -> str:
    """Delete a removed pass. No user data points at a pass."""
    pass_ = index.passes.pop(source, None)
    if pass_ is None:
        return ABSENT
    if commit:
        pass_.delete()
    return DELETED


def _bury_resort(source: uuid.UUID, *, commit: bool, index: ReferenceIndex) -> str:
    """Delete a removed resort, or detach it when a favourite holds it."""
    resort = index.resorts.pop(source, None)
    if resort is None:
        return ABSENT
    if not resort.favourites.exists():
        if commit:
            resort.delete()
        return DELETED
    if commit:
        with transaction.atomic():
            resort.resort_locations.all().delete()
            Resort.objects.filter(pk=resort.pk).update(source_uuid=None)
    return DETACHED


def _bury_location(source: uuid.UUID, *, commit: bool, index: ReferenceIndex) -> str:
    """Delete a removed place, or detach it when user data points at it.

    Every link the estate holds to it goes either way: resort roles and
    pins, and region centres. Its weather goes with a deleted row.
    """
    location = index.locations.pop(source, None)
    if location is None:
        return ABSENT
    users = (
        location.favourites.exists()
        or location.trips.exists()
        or location.field_observations.exists()
    )
    if commit:
        with transaction.atomic():
            ResortLocation.objects.filter(location=location).delete()
            Resort.objects.filter(location=location).update(location=None)
            MicroRegion.objects.filter(centroid_location=location).update(
                centroid_location=None
            )
            if users:
                Location.objects.filter(pk=location.pk).update(source_uuid=None)
            else:
                location.delete()
        for region in index.regions.values():
            if region.centroid_location_id == location.pk:
                region.centroid_location_id = None
    return DETACHED if users else DELETED


_BURY = {
    "locations": _bury_location,
    "passes": _bury_pass,
    "resorts": _bury_resort,
}


def apply_tombstone(
    kind: str, item: dict[str, Any], *, commit: bool, index: ReferenceIndex
) -> str:
    """Remove the row a tombstone names, or detach it when user data needs it.

    Args:
        kind: ``locations``, ``passes`` or ``resorts``.
        item: The tombstoned item.
        commit: Write; otherwise only decide.
        index: The run's index, updated to match.

    Returns:
        ``deleted``, ``detached`` or ``absent`` (not held here).

    """
    return _BURY[kind](_uuid(item["uuid"]), commit=commit, index=index)


# ---------------------------------------------------------------------------
# Weather and day ratings
# ---------------------------------------------------------------------------


def reference_weather_lookups(
    items: list[dict[str, Any]], *, planned: set[str] | None = None
) -> PageLookups:
    """Return a v2 weather page's lookups, its places resolved by service uuid.

    ``PageLookups.locations`` is keyed by the item's own ``location``
    string, so ``apply_weather`` reads it unchanged.

    Args:
        items: One page of v2 weather items.
        planned: Service uuids of places a dry run has counted as created,
            so their days count as created rather than failing.

    Returns:
        The page's lookups.

    """
    wanted: set[uuid.UUID] = set()
    for item in items:
        try:
            wanted.add(uuid.UUID(str(item.get("location"))))
        except ValueError:
            continue
    locations = {
        str(loc.source_uuid): loc
        for loc in Location.objects.filter(source_uuid__in=wanted)
    }
    return PageLookups(
        locations=locations,
        weather=_weather_days(items, locations),
        planned=set() if planned is None else planned,
    )


def apply_reference_day_rating(item: dict[str, Any], *, commit: bool) -> str:
    """Apply one of the service's day ratings (``apply_day_rating_item``).

    Args:
        item: A v2 ``day-ratings`` item.
        commit: Write; otherwise only decide.

    Returns:
        ``created``, ``updated``, ``unchanged``, ``recomputed`` or ``skipped``.

    """
    return apply_day_rating_item(item, commit=commit)


__all__ = [
    "ABSENT",
    "CREATED",
    "DELETED",
    "DETACHED",
    "UNCHANGED",
    "UPDATED",
    "ReferenceIndex",
    "apply_reference_day_rating",
    "apply_reference_location",
    "apply_reference_pass",
    "apply_reference_resort",
    "apply_tombstone",
    "reference_weather_lookups",
]
