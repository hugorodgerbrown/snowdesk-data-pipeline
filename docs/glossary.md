---
name: glossary
description: Domain term → code symbol map — CAAML, DPBRA, massif, Bulletin/RegionBulletin, render model, day rating, sentinels
status: current
last-reviewed: 2026-06-10
---

# Glossary — domain terms to code symbols

One line per term: what it means here, and where it lives in code. When a doc,
a template, and a conversation use different words for the same thing, this
file states the canonical name. Update it whenever a new domain term acquires
a code symbol.

## Formats and sources

| Term | Meaning | Code |
|------|---------|------|
| CAAML | Open avalanche-bulletin interchange standard ("CAA Markup Language"); this project consumes **CAAML v6 JSON** | consumed throughout `bulletins/` |
| SLF | Swiss Institute for Snow and Avalanche Research — primary provider | `bulletins/services/slf_fetcher.py` |
| ALBINA | EUREGIO (Tyrol/South Tyrol/Trentino) avalanche service | `bulletins/services/albina_fetcher.py` |
| Météo-France | French provider; serves DPBRA XML, not CAAML | `bulletins/services/meteofrance_fetcher.py` |
| DPBRA | Météo-France's public avalanche-risk-bulletin product (XML, one document per massif) at `public-api.meteofrance.fr/public/DPBRA/v1/` | translated to CAAML v6 JSON by `bulletins/services/meteofrance_translator.py` |
| Source | How a bulletin's provider is recorded: `Bulletin.Source` TextChoices — `"slf"`, `"albina"`, `"meteofrance"` | `bulletins/models.py` |
| GeoJSON Feature envelope | Raw bulletins are stored wrapped as `{type: "Feature", geometry: null, properties: <raw CAAML>}` | `upsert_bulletin()` in `bulletins/services/slf_fetcher.py` (shared by all fetchers) |
| EAWS | European Avalanche Warning Services — defines the region hierarchy and the 1–5 danger scale | `regions/fixtures/eaws_{CH,AT,FR,IT}.json` |
| Massif | Météo-France's mountain-region unit (e.g. `CHABLAIS`); slug → `FR-NN` region id | `bulletins/services/meteofrance_massifs.py` (`SLUG_TO_CODE`, `slug_to_region_id()`) |

## Regions

EAWS hierarchy: L1 (`MajorRegion`) → L2 (`SubRegion`) → L4 (`MicroRegion`);
L3 is deliberately skipped. All in `regions/models.py`.

| Term | Meaning |
|------|---------|
| MajorRegion | L1, e.g. `CH-4` (Valais); `prefix` unique, boundary derived from children |
| SubRegion | L2, e.g. `CH-41` (Lower Valais); FK to major |
| MicroRegion | L4 warning region, e.g. `CH-4115` or `FR-68` — the unit bulletins, ratings, and subscriptions attach to; `region_id` unique |
| Resort | Ski resort geocoded onto a MicroRegion |
| Region id formats | `CH-4115` (4-digit), `FR-01` (2-digit), `AT-07-23-02` / `IT-32-BZ-15` (multi-level) |

## Bulletins and ratings

| Term | Meaning | Code |
|------|---------|------|
| Bulletin | One CAAML bulletin from one provider for one validity window; `bulletin_id` globally unique; holds `raw_data` + `render_model` | `bulletins/models.py` |
| RegionBulletin | Through table (bulletin, MicroRegion); snapshots `region_name_at_time` because region names drift | `bulletins/models.py` |
| PipelineRun | One execution of an ingestion run — status, timings, created/updated/failed counts | `bulletins/models.py` |
| Render model | Versioned presentation JSON built at ingest so templates contain no derivation logic; `RENDER_MODEL_VERSION = 7` | `bulletins/services/render_model.py` (`build_render_model()`); stored on `Bulletin.render_model` |
| Danger rating | EAWS 1–5 scale (low → very_high) plus `no_snow` / `no_rating` | `DangerRatingValue` in `bulletins/schema.py`; numeric map `_DANGER_NUMBER` in `render_model.py` |
| Subdivision | SLF's +/=/− refinement of a level (e.g. `4-`) | carried through render model and `RegionDayRating.subdivisions` |
| RegionDayRating | Denormalised per-(region, date) min/max rating from the authoritative bulletin; feeds the calendar and CSV export | `bulletins/models.py`; built by `bulletins/services/day_rating.py` |
| Peak rating | The single rating shown when a compressed view (choropleth, tooltip, calendar tile) must collapse a split day — see [compressed-views-rating-rule.md](compressed-views-rating-rule.md) | `public/headlines.py` |
| Day character | Five-way classification of a bulletin day (stable / manageable / hard_to_read / widespread / dangerous) | `compute_day_character()` in `bulletins/services/render_model.py`; spec in [day_character_rules_spec.md](day_character_rules_spec.md) |
| Avalanche problem | EAWS problem token (new_snow, wind_slab, …) with rating, aspects, elevation | `AvalancheProblem` dataclass in `bulletins/schema.py` |
| Elevation band | Per-rating altitude banding (ALBINA / Météo-France only — SLF has none) | `RegionDayRating.bands` JSON |
| Unscheduled bulletin | Out-of-cycle update flagged by the provider | `Bulletin.unscheduled` |

## Weather

| Term | Meaning | Code |
|------|---------|------|
| WeatherSnapshot | Open-Meteo weather for one (region, date): WMO `weather_code` 0–99, sunrise/sunset | `bulletins/models.py`; fetched by `bulletins/services/weather_fetcher.py` |
| is_day projection | Render-time check that "now" falls between that region's sunrise and sunset — never stored | `is_day()` in `bulletins/services/weather_display.py` |
| Bulletin header | Context dict for `templates/includes/bulletin_header.html` ("weather header" is its historical name) | `bulletin_header_context()` in `bulletins/services/weather_display.py` |

## Subscriptions and tracking

| Term | Meaning | Code |
|------|---------|------|
| Subscriber | The custom user model (`AUTH_USER_MODEL`) — email-keyed, pending/active status | `subscriptions/models.py` |
| EncryptedEmailField | Custom `EmailField` subclass that stores AES-256-SIV ciphertext in a `text` column; decrypts on read with a legacy-plaintext fallback | `subscriptions/fields.py` |
| Subscription | (Subscriber, MicroRegion) pair driving bulletin emails | `subscriptions/models.py` |
| Signed token | `TimestampSigner` tokens for account access (expiring) and unsubscribe (permanent, encodes `email\|region_id`) | `subscriptions/services/token.py` |
| PasskeyCredential | WebAuthn platform passkey for a subscriber | `subscriptions/models.py` |
| PushSubscription | Web Push endpoint (spike) | `subscriptions/models.py` |
| BulletinShare / BulletinShareClick | Tokenised short share URL and its per-follow click log | `bulletins/models.py` |
| RequestLog | Request-context snapshot (geo, UA, referer) captured at sign-up/sign-in/subscribe/share-click | `core/models.py` |

## Testing

| Term | Meaning | Code |
|------|---------|------|
| Sentinels | Canonical per-provider payload examples — three cases per provider: **A** single-level single-problem, **B** structurally enhanced single rating, **C** split day + multi-problem | `tests/sentinels/{slf,albina,meteofrance}/` (each case has `source.json` + README) |
| Round-trip contract | Every sentinel must pass `build_render_model()` without raising; Météo-France `source.xml` must translate back to its committed `source.json` | `tests/sentinels/test_sentinel_round_trip.py` |
| Fixture days | Hand-picked real-world edge-case payloads (variable day, prose-only day, legacy no-publication-time, …) | `tests/fixtures/sample_*.json` |
