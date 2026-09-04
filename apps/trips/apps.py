"""
apps/trips/apps.py — AppConfig for the trips application.

The trips app owns ``Trip`` and ``TripParticipant``: a route the organiser
already owns, on a named day, at a stated time and place, plus every
account that has joined it. See ``models.py`` for why the snapshot is
copied rather than read through the route FK, and why the organiser gets a
participant row of their own.
"""

from django.apps import AppConfig


class TripsConfig(AppConfig):
    """AppConfig for the trips application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.trips"
    verbose_name = "Trips"
