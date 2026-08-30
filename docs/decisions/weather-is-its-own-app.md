---
name: weather-is-its-own-app
description: Historical — apps/weather and its pinned bulletins_* table names, retired by SNOW-762
status: historical
last-reviewed: 2026-08-30
---

# Weather is its own app (SNOW-654)

> **Retired by SNOW-762 (2026-08-30).** The weather app this record
> describes was stripped whole — models, services, commands, endpoints and
> surfaces — so nothing here constrains the code any more. SNOW-757
> rebuilds weather as a single immutable Location-anchored model; that
> decision is
> [`weather-is-one-immutable-location-row`](weather-is-one-immutable-location-row.md).
> Kept for the *why*, per
> [the README](README.md): a reversed decision is marked historical, not
> deleted.

**Decision.** The Open-Meteo domain — `WeatherSnapshot`, `ForecastCell`,
`ForecastCellWeather`, `ForecastCellWeatherHistory`, the six services that
fetch, quantise and render them, the `fetch_weather` and
`prune_forecast_points` commands, the dev mirror and the admin classes —
lives in `apps/weather/`, not `apps/bulletins/`.

**Why.** `apps/bulletins` held two unrelated domains that shared one
`models.py`, one `admin.py` and one `dev_views.py` and nothing else. There
is no foreign key in either direction between the CAAML bulletin models and
the Open-Meteo ones, and no bulletin-domain service (`render_model`,
`day_rating`, `grouping`, `coverage`, `settled`, or any of the three
provider fetchers) reads weather at all. The models that genuinely depend on
`ForecastCell` are `favourites.Favourite` and `regions.Resort` — neither of
which is a bulletin.

The original placement followed
[`bulletins-regions-split.md`](bulletins-regions-split.md): "everything that
originates from provider APIs lives in `bulletins/`". Provenance turned out
to be the wrong axis. Open-Meteo is a different upstream on a different
cadence with a different failure mode, and grouping by "came from an API"
put two things in one app that never shared a lifecycle.

The `weather_review` and `weather_forecast` fields in `render_model.py` are
**not** part of this domain — they are the bulletin's own CAAML prose, and
they stayed in `bulletins`.

## The database did not move

Renaming `bulletins_weathersnapshot` to `weather_weathersnapshot` is a
separate ticket. This one moved code only:

SNOW-703 later renamed three of these models to `ForecastCell*`, again
without touching a table — so the names below are pinned twice over, and
a `bulletins_forecastpoint*` table now backs a `weather.ForecastCell*`
model. Both pins are asserted in `tests/weather/test_migrations.py`.

| Model | `db_table` |
|---|---|
| `WeatherSnapshot` | `bulletins_weathersnapshot` |
| `ForecastCell` | `bulletins_forecastpoint` |
| `ForecastCellWeather` | `bulletins_forecastpointweather` |
| `ForecastCellWeatherHistory` | `bulletins_forecastpointweatherhistory` |

Each model pins its old table name in `Meta.db_table`, and all four
migrations the move generated — `weather/0001`, `bulletins/0018`,
`favourites/0005`, `regions/0016` — wrap their operations in
`migrations.SeparateDatabaseAndState` with an empty `database_operations`
list. `manage.py sqlmigrate` on each prints `BEGIN; -- (no-op) COMMIT;` and
nothing else. `tests/weather/test_migrations.py` asserts the four table
names so a later edit cannot drop a pin by accident.

Splitting the DDL out this way keeps the risky half — a four-table rename
against a live production database — behind its own review, and lets this
change deploy with no lock and no downtime.

## The ContentType rows had to be carried across

`django_content_type` is keyed on `(app_label, model)`, so the four rows
still read `bulletins` after the split. Left alone, the `contenttypes`
`post_migrate` hook mints four fresh rows under `weather` and the old ones
are stranded — taking every `auth_permission` row and every admin
`LogEntry` that points at them, because both hold a `content_type_id`
rather than a label.

`weather/0002_move_content_types` rewrites the label. Migrations run
*before* `post_migrate`, so the rewrite wins and the hook then finds the
rows already present. It is idempotent in both directions (a model that
already has a `weather` row is left alone, and any leftover `bulletins`
duplicate is deleted), reversible, and `elidable=False`. Four rows is not a
bulk data update, so it does not breach the project's
"no large dataset updates in migrations" rule.

## Consequences

- Admin URL names are derived from the app label, so
  `admin:bulletins_weathersnapshot_changelist` became
  `admin:weather_weathersnapshot_changelist`, and the changelist override
  template moved to `apps/weather/templates/admin/weather/weathersnapshot/`.
- `fetch_weather` keeps one cross-app import: `Bulletin`, for
  `earliest_valid_from_date()`. The weather backfill window legitimately
  defaults to the start of the bulletin archive, because weather is only
  useful for days a bulletin exists for.
- `seed_test_data` stayed in `apps/bulletins/management/commands/`. It
  already spans five apps (it seeds `auth.User`, `accounts.Account` and
  `favourites.Favourite`), so it is a cross-app dev-fixture command rather
  than a bulletin one, and its `SeedModel` values are unqualified model
  names — no CLI surface changed.
- Environment variable names are unchanged (`OPEN_METEO_*`,
  `FETCH_WEATHER_ADD_HISTORY`, `WEATHER_FETCH_ASYNC`,
  `WEATHER_API_LOCAL_MIRROR_BASE_URL`). Nothing on Render needed editing.
