# DPBRA field coverage — 2026-05-18 sample (35 bulletins + 1 redirects)

## Root element

- `BULLETINS_NEIGE_AVALANCHE` × 35
- `message` × 1

## Root attributes (count = bulletins where attribute is present)

| attribute | count |
|-----------|-------|
| `TYPEBULLETIN` | 35 |
| `ID` | 35 |
| `MASSIF` | 35 |
| `DATEBULLETIN` | 35 |
| `DATEECHEANCE` | 35 |
| `DATEVALIDITE` | 35 |
| `DATEDIFFUSION` | 35 |
| `AMENDEMENT` | 35 |

`AMENDEMENT="true"` occurred in **0** of 35 bulletins.

## Top-level child elements (count = bulletins containing element)

| element | count |
|---------|-------|
| `DateValidite` | 35 |
| `CARTOUCHERISQUE` | 35 |
| `STABILITE` | 35 |
| `QUALITE` | 35 |
| `ENNEIGEMENT` | 35 |
| `NEIGEFRAICHE` | 35 |
| `METEO` | 35 |
| `BSH` | 35 |
| `TexteMeta` | 35 |

## `<RISQUE>` attribute presence (non-empty value)

| attribute | count |
|-----------|-------|
| `RISQUE1` | 35 |
| `RISQUEMAXI` | 35 |
| `COMMENTAIRE` | 35 |
| `RISQUEMAXIJ2` | 35 |
| `DATE_RISQUE_J2` | 35 |
| `LOC1` | 19 |
| `ALTITUDE` | 19 |
| `RISQUE2` | 19 |
| `LOC2` | 19 |

## Notes for the mapping spec

- Root tag is `<BULLETINS_NEIGE_AVALANCHE>` (plural) with the bulletin
  attributes on the root itself — there is no inner `<BULLETIN>` wrapper.
  Update `docs/meteofrance-mapping.md` accordingly.
- Attributes that appear < 36 times are optional in practice; the
  parser must tolerate absence.
- `RISQUE2`/`LOC2`/`ALTITUDE` indicate an elevation-split rating;
  bulletins without those attributes use a single rating for the
  whole massif.
