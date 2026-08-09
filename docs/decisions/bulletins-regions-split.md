---
name: bulletins-regions-split
description: Why apps/bulletins/ and apps/regions/ are separate apps, and why apps/core/ holds the shared abstract bases
status: current
last-reviewed: 2026-08-09
---

# App split: apps/bulletins/ vs apps/regions/ (with apps/core/ as neutral ground)

**Decision.** Geographic reference data (`MicroRegion`, `MajorRegion`,
`SubRegion`, `Resort`) lives in `regions/`; everything that originates from
the three CAAML bulletin providers — bulletins, pipeline runs, day ratings
— lives in `bulletins/`. Shared abstract bases (`BaseModel`) live in
`core/`.

**Amended by SNOW-654.** The Open-Meteo weather models originally landed
in `bulletins/` under this rule ("provider-originated"). They were split
out to `weather/` in August 2026 because provenance turned out to be the
wrong axis: weather shares no foreign key and no service with the
bulletin domain, so the two never had a lifecycle in common. See
[`weather-is-its-own-app.md`](weather-is-its-own-app.md).

**Why.** The two datasets have different lifecycles: regions are stable
fixture-maintained lookup data that changes a few times a season; bulletin
data is high-churn ingest output rewritten daily. Separating them keeps
fixture maintenance, migrations, and ownership boundaries clean. `core/`
exists so neither app needs to import abstract bases from the other,
avoiding a dependency direction between them.

**Consequences.** New models sourced from a CAAML bulletin provider go in
`bulletins/`, even when they reference regions. A model from a
*different* upstream, with its own services and no foreign key into the
bulletin tables, gets its own app instead. New reference/lookup data goes in `regions/`
with a fixture-maintenance command. Neither app may import models from the
other at module level except via explicit FK references.
