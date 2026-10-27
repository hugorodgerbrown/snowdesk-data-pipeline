"""
ingest — Fetching, normalising and serving the providers' data (SNOW-1247).

The one part of Snowdesk that talks to the outside data providers: the three
avalanche-bulletin services (SLF, ALBINA, Météo-France) and Open-Meteo. It is
built to be **rehostable**: copy ``apps/ingest`` into an empty Django project
and it runs, because it imports nothing from any other ``apps.*`` package.
``tests/ingest/test_boundary.py`` fails the build on any such import, and on
any module elsewhere that reaches past ``apps.ingest.public``.

Production is still the only platform that fetches from the providers; staging
and cloud dev environments pull from production's feed. Read
``docs/decisions/ingestion-is-a-rehostable-unit.md`` before changing the
boundary.
"""
