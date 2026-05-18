# DPBRA `SitAvalTyp` — values observed (2026-05-18 sample)

Sample size: 36 bulletins

## SAT codes (frequency = appearances across SAT1 ∪ SAT2)

| SAT code | count |
|----------|-------|
| 2 | 21 |
| 4 | 18 |
| 6 | 9 |

## (SAT1, SAT2) pairs

| SAT1 | SAT2 | count |
|------|------|-------|
| 2 | 4 | 12 |
| 6 | ∅ | 9 |
| 2 | ∅ | 8 |
| 4 | ∅ | 5 |
| 4 | 2 | 1 |

## Characteristic `<TITRE>` text by SAT code

First three bulletins per code, to seed the EAWS-problem mapping.

### SAT = `2` (21 occurrences)

- [SAT2] **Chablais** — Les chutes de neige récente se stabilisent progressivement.
- [SAT1] **Mont-Blanc** — Manteau neigeux hivernal en altitude avec 50cm/1m de neige récente au-dessus de 3000 m.
- [SAT1] **Beaufortain** — RARES INSTABILITES VENTéE EN ALTITUDE

### SAT = `4` (18 occurrences)

- [SAT1] **Chablais** — Les chutes de neige récente se stabilisent progressivement.
- [SAT1] **Aravis** — Les chutes de neige récente se stabilisent progressivement.
- [SAT2] **Mont-Blanc** — Manteau neigeux hivernal en altitude avec 50cm/1m de neige récente au-dessus de 3000 m.

### SAT = `6` (9 occurrences)

- [SAT1] **Chartreuse** — DE MOINS EN MOINS DE NEIGE... RARE COULEE HUMIDE ?
- [SAT1] **Vercors** — DE MOINS EN MOINS DE NEIGE... RARE COULEE HUMIDE ?
- [SAT1] **Devoluy** — RARES INSTABILITÉS EN PLACE EN HAUTE ALTITUDE.

## Next step
Look up each numeric code in the Meteo-France 2026 avalanche guide and map it to the closest EAWS *avalanche problem* (`new_snow`, `wind_slab`, `persistent_weak_layer`, `wet_snow`, `gliding_snow`, `favourable`, `no_distinct`). Populate the `SAT_TO_EAWS` lookup in `docs/meteofrance-mapping.md` once confirmed.

**Caveat:** this is a single end-of-season day. Codes outside {2, 4, 6} are not represented. Re-run the survey against a peak-season day (Jan-Feb) before freezing the lookup.