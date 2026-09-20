"""
tests/trips/test_admin.py — Admin registration checks for the trips app.

Verifies that Trip and TripParticipant are registered, and — the reason
this module exists — that EVERY field in ``_SNAPSHOT_FIELDS`` is
read-only in ``TripAdmin``.

``readonly_fields`` is an explicit list, so a field added to the snapshot
does not become read-only on its own. SNOW-995 added ``duration`` and the
admin list was not updated with it, which a review bot caught; the same
gap had already been there since SNOW-962 added ``slope_samples``. Both
are one-line omissions that let staff overwrite a route-derived figure
independently of its siblings, against the module's stated read-mostly
policy, and neither looks wrong in a diff.

Asserting against ``_SNAPSHOT_FIELDS`` rather than a hand-written list is
the point: the next field added to the snapshot fails here until it is
made read-only, so the invariant maintains itself.
"""

from __future__ import annotations

from django.contrib import admin

from apps.trips.admin import TripAdmin, TripParticipantAdmin
from apps.trips.models import Trip, TripParticipant
from apps.trips.services.trips import _SNAPSHOT_FIELDS


class TestTripAdminRegistration:
    """TripAdmin is registered and configured read-mostly."""

    def test_trip_is_registered(self) -> None:
        """Trip is registered with the default admin site."""
        assert Trip in admin.site._registry

    def test_admin_class_is_trip_admin(self) -> None:
        """The registered admin class is TripAdmin."""
        assert isinstance(admin.site._registry[Trip], TripAdmin)

    def test_participant_is_registered(self) -> None:
        """TripParticipant is registered with its own admin class."""
        assert isinstance(admin.site._registry[TripParticipant], TripParticipantAdmin)

    def test_list_display_names_the_row(self) -> None:
        """The day, the time and the organiser are what identify a trip."""
        registered = admin.site._registry[Trip]
        for column in ("created_by", "name", "route_name", "date", "start_time"):
            assert column in registered.list_display

    def test_list_display_excludes_the_json_blobs(self) -> None:
        """A JSON blob in a changelist column identifies nothing."""
        registered = admin.site._registry[Trip]
        assert "points" not in registered.list_display
        assert "bounds" not in registered.list_display


class TestEverySnapshotFieldIsReadOnly:
    """The snapshot is the trip, so staff must not be able to edit it.

    A snapshot field is copied from the source route at creation and never
    re-read. An editable one could be saved to a value the route never
    held, which would make the trip page show figures belonging to no
    track at all — and it would do so silently, because nothing else reads
    the route again to notice.
    """

    def test_no_snapshot_field_is_editable(self) -> None:
        """Checked against _SNAPSHOT_FIELDS so the list cannot drift.

        ``duration`` is a Route PROPERTY and a Trip FIELD, which is why it
        appears in the snapshot tuple at all; it is a real column here and
        belongs in readonly_fields like every one of its siblings.
        """
        registered = admin.site._registry[Trip]

        missing = [
            field
            for field in _SNAPSHOT_FIELDS
            if field not in registered.readonly_fields
        ]

        assert not missing, (
            f"snapshot fields editable in TripAdmin: {missing}. "
            "Add them to TripAdmin.readonly_fields."
        )
