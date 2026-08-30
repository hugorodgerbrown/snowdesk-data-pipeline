"""
apps/weather — migration history only.

SNOW-762 stripped the Open-Meteo domain: the models, services, commands,
admin, dev mirror and every surface that read them are gone, and this
package holds nothing but its own migrations.

**It cannot simply be deleted.** Six historical migrations in four other
apps declare a dependency on this app's migrations — ``bulletins/0018``,
``favourites/0005``/``0006``/``0007``, ``locations/0001_initial`` and
``regions/0016`` — because ``ForecastCell`` was a foreign-key target in
all three of those domains and moved app label in SNOW-654. Removing
``apps/weather/migrations/`` makes ``migrate`` fail on a fresh database
with ``NodeNotFoundError``, which is every CI run. Django's own answer to
removing an app whose history others depend on is to keep the history
and drop the models, which is what ``0006_delete_weather_models`` does.

The package goes for real once those six migrations have been squashed
away — a migration-history rewrite, which needs its own ticket and a live
database reset (``docs/runbooks/reset-live-db.md``), not a side effect of
this one.
"""
