"""
data_feed — Production's read-only data feed, and the command that pulls it.

Production ingests bulletins (three providers) and weather (Open-Meteo) on
a schedule, and holds the curated resort estate edited in its admin and map
editor. This app makes that data available to other services over HTTPS
(SNOW-1197): GET endpoints under ``/api/data/v1/``, authenticated by OAuth 2
client credentials. Staging is the first client; ``pull_production_data``
is the client side. No models of its own — it reads the bulletins, weather,
locations and regions tables, and never a user-owned one. See
``docs/data-feed.md``.
"""
