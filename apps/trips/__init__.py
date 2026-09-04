"""
apps/trips/ — shareable trips (SNOW-819).

A ``Route`` is a polyline one account owns. A ``Trip`` is what a group
actually plans: that route, on a named day, meeting somewhere at a stated
time — authored by one account (the organiser) and read, then joined, by
others.

A trip is the first Snowdesk object that is interactive and multi-user.
Everything the map holds is one account's own data drawn on shared
reference data; a trip is one row with a roster, and the roster is the
point. See ``docs/decisions/a-trip-is-one-object-with-a-roster.md``.
"""
