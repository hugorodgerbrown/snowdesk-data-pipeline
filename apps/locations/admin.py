"""
apps/locations/admin.py — Django admin registrations for the locations app.

Admin is the **only** surface ``Location`` has until SNOW-702, and it is the
curation surface the whole follow-on data ticket (SNOW-701) depends on, so
it is load-bearing rather than boilerplate.

Two registrations plus one inline:

* ``LocationAdmin`` — the estate, searchable by name and filterable by kind,
  with the resort links inline so a curator can see at a glance that Mont
  Fort is referenced by four resorts before trying to delete it.
* ``ResortLocationAdmin`` — the links in their own right, filterable by
  role, for auditing which resorts still have no top.
* ``ResortLocationInline`` — exported for ``apps/regions/admin.py`` to hang
  off ``ResortAdmin``, so a curator opens Verbier and adds its village and
  its top without leaving the page. It lives here, beside the model it
  edits, and is imported there rather than the other way round.

**And the weather-coverage surface (SNOW-731).** ``LocationAdmin`` carries a
coverage column, a "has gaps" filter, and the estate's first admin action —
"Backfill missing weather". The three go together: an action that fills gaps
is a tool for a problem nobody can see until the changelist says which rows
are incomplete.

The action runs **inline in the request**, capped at
``backfill.ADMIN_MAX_LOCATIONS``. It is not enqueued, and that is a decision
rather than an omission — production runs one ``db_worker`` serving the
default queue, which is also where subscription email goes (invariant 3), so
a long backfill task would head-of-line block outbound mail for its whole
duration. See docs/decisions/weather-backfill-is-an-admin-action.md.
"""

import logging
from typing import Any

from django.contrib import admin, messages
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest

from apps.weather.services import backfill

from .models import Location, ResortLocation

logger = logging.getLogger(__name__)

# The annotation the coverage column reads and the gaps filter compares
# against. Named once so the column, the filter and the ordering cannot
# drift apart.
WEATHER_DAYS = "weather_days"


class ResortLocationInline(admin.TabularInline):
    """Inline for editing a resort's locations from the resort change form.

    Hung off both ``LocationAdmin`` (which resorts reference this place) and
    ``ResortAdmin`` in ``apps/regions/admin.py`` (which places this resort
    references) — the same rows read from either end.
    """

    model = ResortLocation
    extra = 1
    fields = ["location", "role", "is_primary"]
    autocomplete_fields = ["location"]


class ResortLocationForLocationInline(ResortLocationInline):
    """The same inline read from the location's side.

    Shows which resorts reference this location, which is the sharing the
    model exists for — and the thing a curator needs to see before deleting
    a row that four resorts point at.
    """

    fields = ["resort", "role", "is_primary"]
    autocomplete_fields = ["resort"]


class WeatherGapFilter(admin.SimpleListFilter):
    """Filter the estate by whether its weather history is complete.

    "Complete" means a row for every day in the backfill window — the
    configured floor through yesterday — which is exactly the window the
    "Backfill missing weather" action works to. The two are derived from the
    same bounds so the filter cannot promise work the action will not do.
    """

    title = "weather coverage"
    parameter_name = "weather_gaps"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        """Return the filter's options.

        Args:
            request: The admin request.
            model_admin: The admin the filter is mounted on.

        Returns:
            The ``(value, label)`` pairs shown in the sidebar.

        """
        return [("gaps", "Has gaps"), ("complete", "Complete")]

    def queryset(
        self, request: HttpRequest, queryset: QuerySet[Location]
    ) -> QuerySet[Location] | None:
        """Narrow the changelist to the chosen coverage state.

        Args:
            request: The admin request.
            queryset: The annotated changelist queryset.

        Returns:
            The filtered queryset, or None when no option is selected.

        """
        expected = backfill.expected_days()
        if self.value() == "gaps":
            return queryset.filter(**{f"{WEATHER_DAYS}__lt": expected})
        if self.value() == "complete":
            return queryset.filter(**{f"{WEATHER_DAYS}__gte": expected})
        return None


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    """Admin view for Location — the curation surface for the estate."""

    list_display = [
        "name",
        "kind",
        "latitude",
        "longitude",
        "elevation_m",
        "weather_coverage",
        "created_at",
    ]
    list_filter = ["kind", WeatherGapFilter]
    actions = ["backfill_missing_weather"]
    # Empty-name rows are the anonymous ones minted from favourites and
    # observations; ordering by name puts the curated estate together.
    ordering = ["name", "-created_at"]
    search_fields = ["name"]
    inlines = [ResortLocationForLocationInline]
    readonly_fields = [
        "id",
        "uuid",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (None, {"fields": ("name", "kind")}),
        ("Position", {"fields": ("latitude", "longitude")}),
        (
            "Resolved out-of-band",
            {
                "fields": ("elevation_m",),
                "description": (
                    "Filled by an out-of-band resolution pass, which needs "
                    "an Open-Meteo elevation call and so cannot run on save."
                ),
            },
        ),
        ("Metadata", {"fields": ("id", "uuid", "created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Location]:
        """Annotate each row with its weather-day count over the backfill window.

        Counting the whole ``weather`` relation would answer "does this have
        any rows", which is not the question — today's row alone would read
        as coverage. Bounding the count to the floor-to-yesterday window
        makes the number directly comparable with
        ``backfill.expected_days()``.

        Args:
            request: The admin request.

        Returns:
            The changelist queryset, annotated with ``weather_days``.

        """
        annotated: QuerySet[Location] = (
            super()
            .get_queryset(request)
            .annotate(
                **{
                    WEATHER_DAYS: Count(
                        "weather",
                        filter=Q(
                            weather__observed_on__gte=backfill.backfill_floor(),
                            weather__observed_on__lte=backfill.backfill_until(),
                        ),
                        distinct=True,
                    )
                }
            )
        )
        return annotated

    @admin.display(description="Weather", ordering=WEATHER_DAYS)
    def weather_coverage(self, obj: Location) -> str:
        """Return the row's weather coverage as ``held / expected``.

        Args:
            obj: The annotated location.

        Returns:
            A string like ``"203 / 203"``, or ``"—"`` when the backfill
            window is empty (which only happens if the floor is set to today
            or later).

        """
        expected = backfill.expected_days()
        if not expected:
            return "—"
        return f"{getattr(obj, WEATHER_DAYS, 0)} / {expected}"

    @admin.action(description="Backfill missing weather")
    def backfill_missing_weather(
        self, request: HttpRequest, queryset: QuerySet[Location]
    ) -> None:
        """Fill every missing day for the selected locations, inline.

        Capped at ``backfill.ADMIN_MAX_LOCATIONS`` per run: the work happens
        in the request, and a large selection would time the response out
        rather than doing more. What the cap dropped is reported, so the
        operator knows to select the rest rather than assuming it ran.

        Args:
            request: The admin request.
            queryset: The selected locations.

        """
        selected = list(queryset.order_by("name", "-created_at"))
        batch = selected[: backfill.ADMIN_MAX_LOCATIONS]
        skipped = selected[backfill.ADMIN_MAX_LOCATIONS :]

        lines: list[str] = []
        counts = backfill.backfill_locations(
            batch,
            commit=True,
            on_result=lambda location, result: lines.append(
                f"{location}: {result.filled} day(s) filled, "
                f"{result.already_present} already present, "
                f"{result.unresolved} unresolved"
            ),
        )

        for line in lines:
            self.message_user(request, line, level=messages.INFO)

        if counts["failed"]:
            self.message_user(
                request,
                f"{counts['failed']} location(s) failed — see the logs.",
                level=messages.ERROR,
            )
        if skipped:
            self.message_user(
                request,
                f"{len(skipped)} location(s) not processed: this action is "
                f"capped at {backfill.ADMIN_MAX_LOCATIONS} per run so the "
                f"request cannot time out. Select them and run it again.",
                level=messages.WARNING,
            )


@admin.register(ResortLocation)
class ResortLocationAdmin(admin.ModelAdmin):
    """Admin view for ResortLocation — the resort-to-location links."""

    list_display = ["resort", "location", "role", "is_primary", "created_at"]
    list_filter = ["role", "is_primary"]
    search_fields = ["resort__name", "location__name"]
    ordering = ["resort__name", "role"]
    autocomplete_fields = ["resort", "location"]
    readonly_fields = ["id", "uuid", "created_at", "updated_at"]
