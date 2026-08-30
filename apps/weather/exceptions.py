"""
apps/weather/exceptions.py — Exceptions raised by the weather domain.

A module of its own so ``models.py`` and ``services/`` can both import
``ImmutableWeatherRowError`` without either importing the other. The
model's ``save()`` backstop and the ``upsert_weather`` service enforce the
same rule from opposite ends, so both need the exception and neither
should own it.
"""

from __future__ import annotations


class ImmutableWeatherRowError(Exception):
    """Raised on an attempt to rewrite a ``Weather`` row whose day has passed.

    A ``Weather`` row records what was known about a location on one day.
    Once that day is past the record stands: rewriting it would replace an
    account of what we said with an account of what turned out to be true,
    silently and with no way to tell the two apart afterwards.

    Raised rather than skipped deliberately. The bug this model replaces
    (SNOW-628) hid for months because the write path degraded quietly; a
    caller that tries to rewrite history has a bug, and it should say so.
    """
