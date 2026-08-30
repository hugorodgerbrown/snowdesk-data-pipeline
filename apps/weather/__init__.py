"""
apps/weather — the Open-Meteo domain.

One model, ``Weather``: what was known about one ``locations.Location`` on
one day, never rewritten once that day is past. One fetch, ``fetch_weather``,
which walks ``Location.objects.active()`` once and writes one row per
location per run.

**Why weather is its own app rather than part of ``locations``.** The
reasons in ``docs/decisions/weather-is-its-own-app.md`` survived the
collapse from four models to one: a different upstream (Open-Meteo, not the
CAAML providers), a different cadence (a 4×/day scheduled batch), a
different failure mode (a rate limit and a billable call count), and no
foreign key to bulletins in either direction. Putting ``Weather`` in
``apps/locations/`` would make the locations app — the domain primitive
every other app reaches through — depend on Open-Meteo fetch semantics it
has no reason to know about.

**The migration history predates the models.** ``0001``–``0006`` belong to
the estate SNOW-762 stripped (``WeatherSnapshot``, ``ForecastCell``,
``ForecastCellWeather``, ``ForecastCellWeatherHistory``) and are retained
because six migrations in four other apps depend on them —
``bulletins/0018``, ``favourites/0005``/``0006``/``0007``,
``locations/0001_initial`` and ``regions/0016``. ``0007`` creates
``Weather`` on the empty surface ``0006`` left. The old numbers go once
those six have been squashed away, which is a migration-history rewrite
with its own ticket, not a side effect of this one.

Read ``docs/decisions/weather-is-one-immutable-location-row.md`` before
changing the shape of a row.
"""
