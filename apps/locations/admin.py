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
"""

import logging

from django.contrib import admin

from .models import Location, ResortLocation

logger = logging.getLogger(__name__)


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


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    """Admin view for Location — the curation surface for the estate."""

    list_display = [
        "name",
        "kind",
        "latitude",
        "longitude",
        "elevation_m",
        "created_at",
    ]
    list_filter = ["kind"]
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


@admin.register(ResortLocation)
class ResortLocationAdmin(admin.ModelAdmin):
    """Admin view for ResortLocation — the resort-to-location links."""

    list_display = ["resort", "location", "role", "is_primary", "created_at"]
    list_filter = ["role", "is_primary"]
    search_fields = ["resort__name", "location__name"]
    ordering = ["resort__name", "role"]
    autocomplete_fields = ["resort", "location"]
    readonly_fields = ["id", "uuid", "created_at", "updated_at"]
