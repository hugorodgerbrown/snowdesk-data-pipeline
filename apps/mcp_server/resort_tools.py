"""
apps/mcp_server/resort_tools.py — the ops MCP server's resort tools (SNOW-1401).

Four tools that let a staff account read and correct the resort estate from
a chat — Hugo working through printed reference books, one resort at a
time — on the environment the connector points at:

* ``get_resort`` — one resort in full, found by uuid, slug or name, with its
  last few changes from the admin History.
* ``find_resorts`` — a worklist: resorts filtered by region, country, kind,
  a field that is still empty, or ``needs_review``, prominent ones first.
* ``update_resort`` — **writes.** Corrects editorial fields.
* ``create_resort`` — **writes.** Adds a resort at a coordinate.

Production's database is the source of truth for ``Resort``
(``docs/decisions/resorts-are-editable-data.md``), so these write there
directly, and both writes are made deliberate rather than convenient:

* **Preview, then commit.** ``commit`` is required and has no default. A
  preview validates the change and returns the diff, writing nothing. An
  update's commit must quote the preview's ``updated_at``; the row is
  re-read under ``select_for_update`` and the commit is refused if it has
  changed since, so a decision made on a stale diff never lands.
* **Validated by the model.** Values go through ``ResortEditForm``, the
  same constraints the map editor and the admin apply.
* **Sourced and audited.** Every write needs a ``source`` (the book and
  page, say) and records one admin ``LogEntry`` against the staff account
  — every field old → new, and the source — so it shows on the resort's
  admin History, plus a WARNING log line carrying ids only (no values, no
  email address).

``slug``, ``region``, ``canton``, the pin, ``kind``, ``passes`` and the
geocode provenance are not editable here: each has its own writer and its
own rules. Retirement is SNOW-1404. Merged into the ops catalogue by
``apps.mcp_server.ops_tools``.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone
from rapidfuzz import fuzz

from apps.mcp_server import resolvers
from apps.mcp_server.normalise import normalise
from apps.mcp_server.tools import ToolError, ToolSpec, _require_str
from apps.regions.forms import RESORT_EDIT_FIELDS, ResortEditForm
from apps.regions.models import MicroRegion, Resort
from apps.regions.services.point_match import region_for_point
from apps.regions.services.resort_edit import canton_for_region, nearby_resorts
from apps.regions.services.resort_pin import set_resort_pin

logger = logging.getLogger(__name__)

# Matches the waffle setters' reason: a sentence for the History page.
MAX_SOURCE_LENGTH: int = 500

# The editable fields a resort can lack — every one but the name, which is
# never blank, and needs_review, which is a flag rather than a value.
MISSABLE_FIELDS: tuple[str, ...] = tuple(
    field for field in RESORT_EDIT_FIELDS if field not in ("name", "needs_review")
)

# How close to the best fuzzy score a second resort must come for a name
# to count as ambiguous rather than as a match for the best one.
FUZZY_TIE: float = 5.0

# How many of a resort's admin History entries get_resort returns.
HISTORY_SHOWN: int = 5

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 250

# CORE first, so the resorts the map draws largest lead every worklist.
_TIER_ORDER = Case(
    When(tier=Resort.Tier.CORE, then=Value(0)),
    When(tier=Resort.Tier.STANDARD, then=Value(1)),
    default=Value(2),
    output_field=IntegerField(),
)

# The model's own editorial rule for the sentence, carried into the tool
# description so a client drafting one reads it before it writes.
_WHY_IT_MATTERS = Resort._meta.get_field("why_it_matters")
WHY_IT_MATTERS_MAX: int = _WHY_IT_MATTERS.max_length or 0
WHY_IT_MATTERS_RULE: str = str(_WHY_IT_MATTERS.help_text)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _iso(value: dt.datetime | None) -> str | None:
    """Return ``value`` as ISO 8601, or None."""
    return value.isoformat() if value is not None else None


def _resorts() -> QuerySet[Resort]:
    """Return every resort with what a serialiser reads, loaded."""
    return Resort.objects.select_related(
        "region__subregion__major", "location"
    ).prefetch_related("passes")


def _brief(resort: Resort) -> dict[str, Any]:
    """Return the identifying fields of one resort.

    Args:
        resort: A resort with ``region`` loaded.

    Returns:
        ``uuid``, ``slug``, ``name``, ``region_id``, ``region_name``,
        ``kind`` and ``tier``.

    """
    return {
        "uuid": str(resort.uuid),
        "slug": resort.slug,
        "name": resort.name,
        "region_id": resort.region.region_id,
        "region_name": resort.region.name,
        "kind": resort.kind,
        "tier": resort.tier,
    }


def _history(resort: Resort) -> list[dict[str, Any]]:
    """Return the resort's latest admin History entries, newest first.

    Args:
        resort: A saved resort.

    Returns:
        Up to ``HISTORY_SHOWN`` entries: when, who, and the change message.

    """
    entries = (
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(Resort),
            object_id=str(resort.pk),
        )
        .select_related("user")
        .order_by("-action_time")[:HISTORY_SHOWN]
    )
    return [
        {
            "at": _iso(entry.action_time),
            "by": entry.user.get_username(),
            "change": entry.get_change_message(),
        }
        for entry in entries
    ]


def _serialise(resort: Resort) -> dict[str, Any]:
    """Return one resort in full, as get_resort answers it.

    Args:
        resort: A resort loaded through :func:`_resorts`.

    Returns:
        Identity, every editable field, the read-only context (canton, pin,
        provenance, passes) and ``updated_at``.

    """
    pin = resort.pin_coordinates
    return {
        **_brief(resort),
        "fields": {field: getattr(resort, field) for field in RESORT_EDIT_FIELDS},
        "name_alt": resort.name_alt,
        "canton": resort.canton,
        "country": resort.region.subregion.major.country,
        "latitude": pin[0] if pin else None,
        "longitude": pin[1] if pin else None,
        "geocode_source": resort.geocode_source,
        "passes": sorted(ski_pass.name for ski_pass in resort.passes.all()),
        "url": resort.get_absolute_url(),
        "updated_at": _iso(resort.updated_at),
    }


def _audit(actor: Any, resort: Resort, action_flag: int, message: str) -> LogEntry:
    """Record one resort write in the admin log and the application log.

    Args:
        actor: The staff user who made the change.
        resort: The resort written (already saved).
        action_flag: ``ADDITION`` or ``CHANGE``.
        message: What changed and why, as the History page should read it.

    Returns:
        The new ``LogEntry``.

    """
    entry: LogEntry = LogEntry.objects.log_actions(
        user_id=actor.pk,
        queryset=[resort],
        action_flag=action_flag,
        change_message=message,
        single_object=True,
    )
    # The values and the account's email stay in the admin log, which is
    # access-controlled; the application log gets ids only.
    logger.warning(
        "ops_mcp: user id=%s %s resort %s (admin log entry %s)",
        actor.pk,
        "created" if action_flag == ADDITION else "changed",
        resort.slug,
        entry.pk,
    )
    return entry


def _source(arguments: dict[str, Any]) -> str:
    """Return the required ``source`` argument, bounded in length.

    Raises:
        ToolError: The source is missing, blank or too long.

    """
    source = _require_str(arguments, "source").strip()
    if len(source) > MAX_SOURCE_LENGTH:
        raise ToolError(f"'source' must be at most {MAX_SOURCE_LENGTH} characters.")
    return source


def _commit(arguments: dict[str, Any]) -> bool:
    """Return the required boolean ``commit`` argument.

    Raises:
        ToolError: ``commit`` is missing or not a boolean.

    """
    value = arguments.get("commit")
    if not isinstance(value, bool):
        raise ToolError("'commit' is required: false to preview, true to write.")
    return value


def _changes(arguments: dict[str, Any], key: str, *, required: bool) -> dict[str, Any]:
    """Return a ``{field: value}`` argument, its names checked.

    Args:
        arguments: The raw ``arguments`` dict.
        key: The argument's name.
        required: Whether the object must be present and non-empty.

    Returns:
        The field values, keyed by field name.

    Raises:
        ToolError: The value is not an object, is empty when required, or
            names a field these tools do not write.

    """
    value = arguments.get(key)
    if value is None and not required:
        return {}
    if not isinstance(value, dict) or (required and not value):
        raise ToolError(f"'{key}' must be an object of field: value pairs.")
    unknown = sorted(set(value) - set(RESORT_EDIT_FIELDS))
    if unknown:
        raise ToolError(
            f"Not editable here: {', '.join(unknown)}. "
            f"Editable fields: {', '.join(RESORT_EDIT_FIELDS)}."
        )
    return value


def _validated(resort: Resort, changes: dict[str, Any]) -> dict[str, Any]:
    """Validate ``changes`` over ``resort`` and return the cleaned values.

    The changes are merged over the row's current values before binding,
    so an absent field is left alone — the map editor's rule.

    Args:
        resort: The resort being edited, or an unsaved one being created.
        changes: The requested values, already name-checked.

    Returns:
        The cleaned value of every editable field.

    Raises:
        ToolError: A value fails the model's validation; every failing
            field is named.

    """
    data = {
        field: changes.get(field, getattr(resort, field))
        for field in RESORT_EDIT_FIELDS
    }
    form = ResortEditForm(data, instance=resort)
    if not form.is_valid():
        problems = "; ".join(
            f"{field}: {' '.join(str(error) for error in errors)}"
            for field, errors in form.errors.items()
        )
        raise ToolError(f"Invalid change — {problems}")
    return {field: form.cleaned_data[field] for field in RESORT_EDIT_FIELDS}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the fields whose value changes, as ``{field: {from, to}}``."""
    return {
        field: {"from": before[field], "to": after[field]}
        for field in RESORT_EDIT_FIELDS
        if before[field] != after[field]
    }


def _sentence_check(values: dict[str, Any]) -> dict[str, Any] | None:
    """Return the length check for ``why_it_matters``, when it is set."""
    sentence = values.get("why_it_matters") or ""
    if not sentence:
        return None
    return {"length": len(sentence), "max_length": WHY_IT_MATTERS_MAX}


def _change_message(diff: dict[str, dict[str, Any]], source: str) -> str:
    """Return the History line for a set of field changes and their source."""
    parts = [
        f"{field}: {change['from']!r} → {change['to']!r}"
        for field, change in diff.items()
    ]
    return f"Via the ops MCP server — {'; '.join(parts)}. Source: {source}"


# ---------------------------------------------------------------------------
# Finding a resort
# ---------------------------------------------------------------------------


def _by_uuid(resorts: QuerySet[Resort], ref: str) -> Resort | None:
    """Return the resort whose ``uuid`` or ``source_uuid`` is ``ref``.

    Returns None when ``ref`` is not a uuid at all.

    Raises:
        ToolError: ``ref`` is a uuid that no resort carries.

    """
    try:
        as_uuid = uuid.UUID(ref)
    except ValueError:
        return None
    found = resorts.filter(Q(uuid=as_uuid) | Q(source_uuid=as_uuid)).first()
    if found is None:
        raise ToolError(f"No resort has uuid {ref}.")
    return found


def _fuzzy(resorts: QuerySet[Resort], name: str) -> list[Resort]:
    """Return the resorts whose name or ``name_alt`` best matches ``name``.

    Scored per resort, with the place search's normaliser, scorer and
    cut-off, rather than through ``resolvers.search_places``: that search
    keeps one resort per region, so two similarly named resorts in one
    region would collapse into one and a guess would pass for a match.
    Every resort within ``FUZZY_TIE`` points of the best score is
    returned, so a near-tie reaches the caller as an ambiguity.

    """
    query = normalise(name)
    if not query:
        return []
    scores: dict[int, tuple[float, Resort]] = {}
    for resort in resorts:
        for candidate in (resort.name, resort.name_alt):
            if not candidate:
                continue
            score = fuzz.WRatio(query, normalise(candidate))
            if (
                score >= resolvers._SCORE_CUTOFF
                and score > scores.get(resort.pk, (0.0, resort))[0]
            ):
                scores[resort.pk] = (score, resort)
    if not scores:
        return []
    best = max(score for score, _resort in scores.values())
    return [resort for score, resort in scores.values() if score >= best - FUZZY_TIE]


def _named(resorts: QuerySet[Resort], name: str) -> list[Resort]:
    """Return the resorts a name points at: exact first, then fuzzy.

    An exact ``name`` or ``name_alt`` match (ignoring case) wins outright;
    failing one, :func:`_fuzzy`'s best matches.

    """
    exact = list(resorts.filter(Q(name__iexact=name) | Q(name_alt__iexact=name)))
    return exact or _fuzzy(resorts, name)


def resolve_resort(ref: str) -> Resort:
    """Return the one resort ``ref`` names: a uuid, a slug or a name.

    A uuid matches the row's own ``uuid`` or its ``source_uuid``; a slug
    matches exactly; a name goes through :func:`_named`.

    Args:
        ref: The reference, as the operator gave it.

    Returns:
        The resort.

    Raises:
        ToolError: Nothing matches, or several resorts do — the candidates
            are listed so the caller can pick one by slug.

    """
    ref = ref.strip()
    resorts = _resorts()
    found = _by_uuid(resorts, ref) or resorts.filter(slug=ref.lower()).first()
    if found is not None:
        return found
    candidates = _named(resorts, ref)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ToolError(f"No resort matches {ref!r}; try find_resorts.")
    listed = ", ".join(
        f"{resort.name} ({resort.region.region_id}, slug {resort.slug})"
        for resort in candidates
    )
    raise ToolError(f"{ref!r} matches several resorts: {listed}. Pass a slug.")


def get_resort(ref: str) -> dict[str, Any]:
    """Return one resort in full, with its latest admin History.

    Args:
        ref: A uuid, slug or name.

    Returns:
        :func:`_serialise`'s fields plus ``history`` and ``summary``.

    """
    resort = resolve_resort(ref)
    result = _serialise(resort)
    result["history"] = _history(resort)
    empty = [field for field, value in result["fields"].items() if value in ("", None)]
    result["empty_fields"] = empty
    result["summary"] = (
        f"{resort.name} ({resort.region.region_id}, {resort.kind}, {resort.tier}); "
        f"{len(empty)} empty field(s)" + (f": {', '.join(empty)}." if empty else ".")
    )
    return result


def _empty(field_name: str) -> Q:
    """Return the filter for resorts where ``field_name`` holds nothing.

    Null for a nullable column, ``""`` for a text one, either for both.

    """
    field = Resort._meta.get_field(field_name)
    empty = Q(**{f"{field_name}__isnull": True}) if field.null else Q(pk__in=[])
    if field.get_internal_type() in ("CharField", "TextField", "URLField"):
        empty |= Q(**{field_name: ""})
    return empty


def _check_filters(
    region_id: str | None, kind: str | None, missing: str | None
) -> None:
    """Refuse a filter value the estate does not have.

    Raises:
        ToolError: An unknown region, kind or field name.

    """
    if (
        region_id is not None
        and not MicroRegion.objects.filter(region_id=region_id).exists()
    ):
        raise ToolError(f"Unknown region_id: {region_id!r}.")
    if kind is not None and kind not in Resort.Kind.values:
        raise ToolError(f"'kind' must be one of: {', '.join(Resort.Kind.values)}.")
    if missing is not None and missing not in MISSABLE_FIELDS:
        raise ToolError(f"'missing' must be one of: {', '.join(MISSABLE_FIELDS)}.")


def _filtered(
    region_id: str | None,
    country: str | None,
    kind: str | None,
    missing: str | None,
    needs_review: bool | None,
) -> QuerySet[Resort]:
    """Return the resorts matching every filter given.

    Raises:
        ToolError: A filter value is not one the estate has.

    """
    _check_filters(region_id, kind, missing)
    resorts = Resort.objects.select_related("region")
    if region_id is not None:
        resorts = resorts.filter(region__region_id=region_id)
    if country is not None:
        resorts = resorts.filter(region__subregion__major__country=country.upper())
    if kind is not None:
        resorts = resorts.filter(kind=kind)
    if missing is not None:
        resorts = resorts.filter(_empty(missing))
    if needs_review is not None:
        resorts = resorts.filter(needs_review=needs_review)
    return resorts


def find_resorts(
    *,
    region_id: str | None = None,
    country: str | None = None,
    kind: str | None = None,
    missing: str | None = None,
    needs_review: bool | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return a worklist of resorts, the prominent ones first.

    Args:
        region_id: Only resorts in this micro-region.
        country: Only resorts in this country (ISO alpha-2).
        kind: Only this ``Resort.Kind``.
        missing: Only resorts where this field is blank or null.
        needs_review: Only resorts with this ``needs_review`` value.
        limit: Most rows to return.

    Returns:
        ``total`` (before the limit), ``resorts`` (brief rows, each with the
        ``missing`` field's value when one was asked about) and ``summary``.

    """
    resorts = (
        _filtered(region_id, country, kind, missing, needs_review)
        .annotate(tier_order=_TIER_ORDER)
        .order_by("tier_order", "name")
    )
    total = resorts.count()
    rows = []
    for resort in resorts[:limit]:
        row = _brief(resort)
        if missing is not None:
            row[missing] = getattr(resort, missing)
        rows.append(row)
    shown = f"showing {len(rows)}" if len(rows) < total else "all shown"
    return {
        "total": total,
        "resorts": rows,
        "summary": f"{total} resort(s) match ({shown}).",
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _expected_updated_at(value: Any) -> dt.datetime:
    """Parse the ``expected_updated_at`` a commit quotes from its preview.

    Raises:
        ToolError: Missing, unparseable or without a timezone.

    """
    if not isinstance(value, str) or not value.strip():
        raise ToolError(
            "A commit needs 'expected_updated_at': the updated_at the preview returned."
        )
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError(f"'expected_updated_at' is not ISO 8601: {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ToolError("'expected_updated_at' must carry a timezone.")
    return parsed


def update_resort(
    ref: str,
    changes: dict[str, Any],
    source: str,
    commit: bool,
    expected_updated_at: dt.datetime | None,
    actor: Any,
) -> dict[str, Any]:
    """Preview or commit a correction to one resort's editorial fields.

    Args:
        ref: A uuid, slug or name.
        changes: ``{field: value}``, editable fields only.
        source: Where the new values come from; recorded with the change.
        commit: False to preview, True to write.
        expected_updated_at: Required with ``commit``: the preview's
            ``updated_at``, so a row changed since is refused.
        actor: The staff user making the change.

    Returns:
        The resort's brief fields, ``diff``, ``committed``, ``updated_at``,
        ``log_entry_id``, ``why_it_matters_check`` and ``summary``.

    Raises:
        ToolError: The resort is unknown or ambiguous, a value is invalid,
            or the row changed since the preview.

    """
    resort = resolve_resort(ref)
    if not commit:
        before = {field: getattr(resort, field) for field in RESORT_EDIT_FIELDS}
        after = _validated(resort, changes)
        diff = _diff(before, after)
        return {
            **_brief(resort),
            "diff": diff,
            "committed": False,
            "updated_at": _iso(resort.updated_at),
            "log_entry_id": None,
            "why_it_matters_check": _sentence_check(after),
            "summary": (
                f"Preview for {resort.name}: {len(diff)} field(s) would change"
                + (f" ({', '.join(diff)})" if diff else "")
                + ". Commit with expected_updated_at to write."
            ),
        }

    if expected_updated_at is None:
        raise ToolError(
            "A commit needs 'expected_updated_at': the updated_at the preview returned."
        )
    with transaction.atomic():
        locked = (
            Resort.objects.select_for_update()
            .select_related("region")
            .get(pk=resort.pk)
        )
        if locked.updated_at != expected_updated_at:
            raise ToolError(
                f"{locked.name} changed at {_iso(locked.updated_at)}, after the "
                "preview this commit quotes. Preview again and confirm the new diff."
            )
        before = {field: getattr(locked, field) for field in RESORT_EDIT_FIELDS}
        after = _validated(locked, changes)
        diff = _diff(before, after)
        entry = None
        if diff:
            for field in diff:
                setattr(locked, field, after[field])
            locked.save(update_fields=[*diff, "updated_at"])
            entry = _audit(actor, locked, CHANGE, _change_message(diff, source))
    return {
        **_brief(locked),
        "diff": diff,
        "committed": bool(diff),
        "updated_at": _iso(locked.updated_at),
        "log_entry_id": entry.pk if entry else None,
        "why_it_matters_check": _sentence_check(after),
        "summary": (
            f"{locked.name}: {len(diff)} field(s) changed ({', '.join(diff)})."
            if diff
            else f"{locked.name}: nothing to change; nothing written."
        ),
    }


def _duplicates(name: str, latitude: float, longitude: float) -> list[dict[str, Any]]:
    """Return the existing resorts a new one at this name and point may repeat.

    The resorts :func:`_named` finds for the name (exact, else the best
    fuzzy matches), and any resort pinned within
    ``DUPLICATE_RADIUS_KM`` — each listed once, with its distance when it
    has a pin.

    """
    found: dict[int, dict[str, Any]] = {}
    for near in nearby_resorts(latitude, longitude):
        found[near.resort.pk] = {
            **_brief(near.resort),
            "why": "nearby",
            "distance_km": round(near.distance_km, 2),
        }
    for resort in _named(Resort.objects.select_related("region"), name):
        found.setdefault(resort.pk, {**_brief(resort), "why": "similar name"})
    return list(found.values())


def create_resort(
    name: str,
    latitude: float,
    longitude: float,
    canton: str,
    fields: dict[str, Any],
    source: str,
    commit: bool,
    actor: Any,
) -> dict[str, Any]:
    """Preview or commit a new resort at a coordinate.

    The region comes from the point; the canton, when not given, from the
    region's resorts. The row is stamped ``geocode_source=IMPORT`` and
    ``needs_review=True`` — a coordinate from a book is data, not an
    operator's placement on the map, so it waits for a confirming pass.

    Args:
        name: The resort's name.
        latitude: The pin's latitude (WGS-84).
        longitude: The pin's longitude (WGS-84).
        canton: The canton, or ``""`` to inherit the region's.
        fields: Further editable fields, ``{field: value}``.
        source: Where the resort comes from; recorded with it.
        commit: False to preview, True to write.
        actor: The staff user creating it.

    Returns:
        The new resort's fields (``uuid`` and ``slug`` only once
        committed), ``region_id``, ``duplicates``, ``committed``,
        ``log_entry_id`` and ``summary``.

    Raises:
        ToolError: The point is outside every region, no canton can be
            found, a value is invalid, or the region already holds a
            resort of that name.

    """
    region = region_for_point(latitude, longitude)
    if region is None:
        raise ToolError(
            f"No avalanche region contains ({latitude:.5f}, {longitude:.5f}); "
            "check the coordinate."
        )
    resolved_canton = canton or canton_for_region(region)
    if not resolved_canton:
        raise ToolError(
            f"{region.region_id} has no resorts to take a canton from; pass 'canton'."
        )
    if len(resolved_canton) > (Resort._meta.get_field("canton").max_length or 0):
        raise ToolError(f"'canton' is too long: {resolved_canton!r}.")

    resort = Resort(
        name=name,
        canton=resolved_canton,
        region=region,
        geocode_source=Resort.GeocodeSource.IMPORT,
        geocoded_at=timezone.now(),
    )
    values = _validated(resort, {**fields, "name": name, "needs_review": True})
    duplicates = _duplicates(name, latitude, longitude)
    same_region = [
        d
        for d in duplicates
        if d["region_id"] == region.region_id and d["name"].lower() == name.lower()
    ]
    preview = {
        "name": name,
        "region_id": region.region_id,
        "region_name": region.name,
        "canton": resolved_canton,
        "latitude": latitude,
        "longitude": longitude,
        "fields": values,
        "duplicates": duplicates,
        "why_it_matters_check": _sentence_check(values),
    }
    if same_region:
        raise ToolError(
            f"{region.region_id} already has a resort called {name} "
            f"(slug {same_region[0]['slug']}); update that one instead."
        )
    if not commit:
        warning = (
            f" {len(duplicates)} possible duplicate(s) — check them first."
            if duplicates
            else ""
        )
        return {
            **preview,
            "committed": False,
            "log_entry_id": None,
            "summary": (
                f"Preview: {name} would be created in {region.name} "
                f"({region.region_id}), canton {resolved_canton}.{warning}"
            ),
        }

    for field, value in values.items():
        setattr(resort, field, value)
    with transaction.atomic():
        resort.save()
        set_resort_pin(resort, latitude, longitude)
        set_fields = [
            f"{field}: {value!r}"
            for field, value in values.items()
            if value not in ("", None)
        ]
        entry = _audit(
            actor,
            resort,
            ADDITION,
            f"Created via the ops MCP server in {region.region_id} at "
            f"({latitude}, {longitude}) — {'; '.join(set_fields)}. Source: {source}",
        )
    return {
        **preview,
        **_brief(resort),
        "committed": True,
        "updated_at": _iso(resort.updated_at),
        "log_entry_id": entry.pk,
        "summary": (
            f"Created {name} in {region.name} ({region.region_id}), slug "
            f"{resort.slug}; flagged needs_review until its pin is confirmed."
        ),
    }


# ---------------------------------------------------------------------------
# JSON-RPC adapters
# ---------------------------------------------------------------------------


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    """Return an optional string argument, stripped, or None when absent.

    Raises:
        ToolError: The value is present but not a string.

    """
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string.")
    return value.strip() or None


def _coordinate(arguments: dict[str, Any], key: str, bound: float) -> float:
    """Return a required coordinate argument within ±``bound``.

    Raises:
        ToolError: Missing, not a number, or out of range.

    """
    value = arguments.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"'{key}' must be a number.")
    if not -bound <= value <= bound:
        raise ToolError(f"'{key}' must be between -{bound} and {bound}.")
    return float(value)


def _limit(arguments: dict[str, Any]) -> int:
    """Return the ``limit`` argument, defaulted and clamped to ``MAX_LIMIT``.

    Raises:
        ToolError: ``limit`` is present but not a positive integer.

    """
    value = arguments.get("limit", DEFAULT_LIMIT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolError("'limit' must be a positive integer.")
    return min(value, MAX_LIMIT)


def _handle_get_resort(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``get_resort`` arguments and call it."""
    return get_resort(_require_str(arguments, "ref"))


def _handle_find_resorts(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``find_resorts`` arguments and call it."""
    needs_review = arguments.get("needs_review")
    if needs_review is not None and not isinstance(needs_review, bool):
        raise ToolError("'needs_review' must be a boolean.")
    return find_resorts(
        region_id=_optional_str(arguments, "region_id"),
        country=_optional_str(arguments, "country"),
        kind=_optional_str(arguments, "kind"),
        missing=_optional_str(arguments, "missing"),
        needs_review=needs_review,
        limit=_limit(arguments),
    )


def _handle_update_resort(arguments: dict[str, Any], actor: Any) -> dict[str, Any]:
    """Validate ``update_resort`` arguments and call it."""
    commit = _commit(arguments)
    return update_resort(
        _require_str(arguments, "ref"),
        _changes(arguments, "changes", required=True),
        _source(arguments),
        commit,
        _expected_updated_at(arguments.get("expected_updated_at")) if commit else None,
        actor,
    )


def _handle_create_resort(arguments: dict[str, Any], actor: Any) -> dict[str, Any]:
    """Validate ``create_resort`` arguments and call it."""
    fields = _changes(arguments, "fields", required=False)
    if "name" in fields or "needs_review" in fields:
        raise ToolError(
            "Pass the name as 'name'; a created resort always needs review."
        )
    return create_resort(
        _require_str(arguments, "name").strip(),
        _coordinate(arguments, "latitude", 90),
        _coordinate(arguments, "longitude", 180),
        (_optional_str(arguments, "canton") or "").upper(),
        fields,
        _source(arguments),
        _commit(arguments),
        actor,
    )


_REF_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": "The resort's uuid, slug or name (a name may be fuzzy).",
}
_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "Where the values come from, e.g. 'Ski Atlas 2024, p. 112'; recorded "
        "with the change in the resort's admin History."
    ),
}
_COMMIT_SCHEMA: dict[str, Any] = {
    "type": "boolean",
    "description": "False previews and writes nothing; true writes.",
}
_FIELDS_DESCRIPTION: str = (
    f"Editable fields: {', '.join(RESORT_EDIT_FIELDS)}. Elevations in metres, "
    "total_piste_km in km, season dates as MM-DD, tier CORE/STANDARD/MINOR. "
    f"why_it_matters is at most {WHY_IT_MATTERS_MAX} characters: "
    f"{WHY_IT_MATTERS_RULE}"
)


RESORT_TOOLS: dict[str, ToolSpec] = {
    "get_resort": ToolSpec(
        name="get_resort",
        description=(
            "Return one resort in full — every editable field, its region, "
            "canton, pin, passes and page URL, which fields are still empty, "
            "and its last few changes from the admin History. Find it by "
            "uuid, slug or name; an ambiguous name lists the candidates."
        ),
        input_schema={
            "type": "object",
            "properties": {"ref": _REF_SCHEMA},
            "required": ["ref"],
        },
        handler=_handle_get_resort,
    ),
    "find_resorts": ToolSpec(
        name="find_resorts",
        description=(
            "List resorts as a worklist, CORE tier first then by name: "
            "filter by region_id, country, kind, a field still empty "
            "('missing', e.g. why_it_matters or num_runs) or needs_review. "
            "Returns uuid, slug, name, region, kind and tier for each."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_id": {"type": "string", "description": "e.g. 'CH-4115'."},
                "country": {"type": "string", "description": "ISO alpha-2, e.g. 'CH'."},
                "kind": {"type": "string", "enum": list(Resort.Kind.values)},
                "missing": {"type": "string", "enum": list(MISSABLE_FIELDS)},
                "needs_review": {"type": "boolean"},
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Most rows (default {DEFAULT_LIMIT}, max {MAX_LIMIT})."
                    ),
                },
            },
        },
        handler=_handle_find_resorts,
    ),
    "update_resort": ToolSpec(
        name="update_resort",
        description=(
            "Correct one resort's editorial fields on this environment. "
            "Always preview first (commit false): it validates the change "
            "and returns the diff and the updated_at to commit against. "
            "Show the operator the diff and commit only when they confirm, "
            "passing that updated_at as expected_updated_at; a resort "
            "changed since the preview is refused. Every commit is recorded "
            "with its source in the resort's admin History. " + _FIELDS_DESCRIPTION
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ref": _REF_SCHEMA,
                "changes": {
                    "type": "object",
                    "description": "{field: new value}; null or '' clears a field.",
                },
                "source": _SOURCE_SCHEMA,
                "commit": _COMMIT_SCHEMA,
                "expected_updated_at": {
                    "type": "string",
                    "description": "Required with commit: the preview's updated_at.",
                },
            },
            "required": ["ref", "changes", "source", "commit"],
        },
        handler=_handle_update_resort,
        writes=True,
    ),
    "create_resort": ToolSpec(
        name="create_resort",
        description=(
            "Add a resort at a coordinate on this environment. The avalanche "
            "region comes from the point and the canton, if omitted, from "
            "the region's other resorts. Always preview first (commit false) "
            "and show the operator any possible duplicates — same or similar "
            "name, or a resort within 2 km — before committing. A created "
            "resort is flagged needs_review until its pin is confirmed on "
            "the map, and recorded with its source in the admin History. "
            + _FIELDS_DESCRIPTION
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The resort's name."},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "canton": {
                    "type": "string",
                    "description": "e.g. 'VS'; omit to inherit the region's.",
                },
                "fields": {
                    "type": "object",
                    "description": "Further editable fields, {field: value}.",
                },
                "source": _SOURCE_SCHEMA,
                "commit": _COMMIT_SCHEMA,
            },
            "required": ["name", "latitude", "longitude", "source", "commit"],
        },
        handler=_handle_create_resort,
        writes=True,
    ),
}
