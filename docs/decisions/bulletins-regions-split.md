---
name: bulletins-regions-split
description: Why apps/bulletins/ and apps/regions/ are separate apps, and why apps/core/ holds the shared abstract bases
status: current
last-reviewed: 2026-06-10
---

# App split: apps/bulletins/ vs apps/regions/ (with apps/core/ as neutral ground)

**Decision.** Geographic reference data (`MicroRegion`, `MajorRegion`,
`SubRegion`, `Resort`) lives in `regions/`; everything that originates from
provider APIs — bulletins, pipeline runs, day ratings, weather — lives in
`bulletins/`. Shared abstract bases (`BaseModel`) live in `core/`.

**Why.** The two datasets have different lifecycles: regions are stable
fixture-maintained lookup data that changes a few times a season; bulletin
data is high-churn ingest output rewritten daily. Separating them keeps
fixture maintenance, migrations, and ownership boundaries clean. `core/`
exists so neither app needs to import abstract bases from the other,
avoiding a dependency direction between them.

**Consequences.** New provider-originated models go in `bulletins/`, even
when they reference regions. New reference/lookup data goes in `regions/`
with a fixture-maintenance command. Neither app may import models from the
other at module level except via explicit FK references.
