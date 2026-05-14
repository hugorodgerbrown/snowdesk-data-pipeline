# EAWS fixture audit — MicroRegion name & Resort FK drift

**Date:** 2026-05-14
**Surface:** `regions/fixtures/eaws.json`, `regions.MicroRegion.name`, `regions.Resort.region`
**Source of truth:** SLF's CAAML bulletin API (`https://aws.slf.ch/api/bulletin-list/caaml/en/json?limit=200`) — 140 unique `regionID → name` pairs across the last ~200 bulletin cycles.

## Bottom line

SLF has substantially renamed (and likely renumbered) their L4 micro-regions since this fixture was last built. **85 of 140 MicroRegions covered by SLF bulletins have stale `name` fields in our DB (~60%).** The geographic polygons appear to match SLF's *current* region_id scheme — only the labels have drifted. Separately, **32 of 91 geocoded resorts (~35%) have FKs pointing to a region whose polygon doesn't contain the resort's coordinates** — the FKs need to be recomputed by spatial join after the name fix.

## Verification — polygons match SLF's current IDs

Spot-checks confirmed our polygons match SLF's *current* region_id assignments, not the (stale) names we have stored:

| Town | Coordinates | Falls inside polygon for | Our DB name | SLF name |
|---|---|---|---|---|
| Niedersimmental valley | `(7.55, 46.65)` | `CH-1221` | `Grindelwald` ❌ | `Niedersimmental` ✓ |
| Grindelwald town | `(8.04, 46.62)` | `CH-1242` | `Innertkirchen` ❌ | `Grindelwald` ✓ |
| Gstaad town | `(7.29, 46.47)` | `CH-1222` | `Lauterbrunnen` ❌ | `Gstaad` ✓ |
| Lauterbrunnen town | `(7.91, 46.59)` | `CH-1233` | `Guttannen` ❌ | `Lauterbrunnen` ✓ |

Pattern: the polygon is right, the name is wrong. **Fix is name-only — no geometry changes needed.**

## Full name-mismatch table (85 rows)

| region_id | DB name (current, stale) | SLF name (current) | DB centroid |
|-----------|--------------------------|---------------------|-------------|
| CH-1111 | Aigle - Yvorne | Waadtländer Voralpen | 6.940, 46.471 |
| CH-1112 | Veveyse - Pays-d'Enhaut | Pays d'Enhaut | 7.137, 46.471 |
| CH-1113 | Leysin - Villars - Château-d'Oex | Aigle-Leysin | 6.996, 46.363 |
| CH-1114 | Les Ormonts - Diablerets | Bex-Villars | 7.077, 46.248 |
| CH-1121 | Gruyère - Moléson | Jaun | 7.205, 46.618 |
| CH-1122 | Jaun - Abländschen | Gruyère | 7.126, 46.668 |
| CH-1211 | Thun - Interlaken | westliche Berner Voralpen | 7.464, 46.799 |
| CH-1212 | Brienz - Brünig | östliche Berner Voralpen | 7.771, 46.868 |
| CH-1221 | Grindelwald | Niedersimmental | 7.448, 46.667 |
| CH-1222 | Lauterbrunnen | Gstaad | 7.308, 46.466 |
| CH-1223 | Saanenland | Wildhorn | 7.297, 46.353 |
| CH-1224 | Kandertal - Adelboden | Lenk | 7.428, 46.483 |
| CH-1225 | Simmental | Iffigen | 7.462, 46.397 |
| CH-1226 | Kander - Glacier | Adelboden | 7.617, 46.584 |
| CH-1227 | Niesen - Frutigen | Engstligen | 7.569, 46.436 |
| CH-1228 | Schilthorn | Obersimmental | 7.488, 46.582 |
| CH-1231 | Grimsel - Oberhasli | Kandersteg | 7.721, 46.595 |
| CH-1232 | Lötschental | Blüemlisalp | 7.736, 46.516 |
| CH-1233 | Guttannen | Lauterbrunnen | 7.882, 46.596 |
| CH-1234 | Meiringen - Hasliberg | Jungfrau - Schilthorn | 7.864, 46.580 |
| CH-1241 | Brünig - Lungern | Brienz-Interlaken | 7.995, 46.706 |
| CH-1242 | Innertkirchen | Grindelwald | 7.988, 46.652 |
| CH-1243 | Susten - Gadmental | Schreckhorn | 8.116, 46.569 |
| CH-1244 | Melchsee - Sarneraa | Hasliberg - Rosenlaui | 8.168, 46.717 |
| CH-1245 | Göscheneralp | Guttannen | 8.219, 46.638 |
| CH-1246 | Furka - Urseren | Gadmertal | 8.374, 46.728 |
| CH-1247 | Ulrichen - Oberwald | Grimselpass | 8.311, 46.584 |
| CH-1311 | Monthey - St-Maurice | Vouvry | 6.842, 46.327 |
| CH-1312 | Fully - Saillon | Monthey-Val d'Illiez | 6.908, 46.163 |
| CH-2111 | Urner Hochalpen | Pilatus | 8.252, 46.949 |
| CH-2112 | Urner Mittelland | Schwarzenberg | 8.250, 47.015 |
| CH-2121 | Engelberg | Glaubenberg | 8.109, 46.858 |
| CH-2122 | Melchtal - Frutt | Engelberg | 8.428, 46.823 |
| CH-2123 | Sarneraatal | Melchtal | 8.261, 46.805 |
| CH-2124 | Pilatus - Vierwaldstättersee | Gersau | 8.480, 46.962 |
| CH-2131 | Rigi - Luzern | Rothenthurm | 8.634, 47.100 |
| CH-2132 | Hochybrig - Mythen | Ybrig | 8.862, 47.076 |
| CH-2133 | Küssnacht - Arth | Stoos | 8.686, 46.997 |
| CH-2134 | Muotatal | Bisistal | 8.828, 46.947 |
| CH-2211 | Ibergeregg - Ybrig | Schächental | 8.702, 46.872 |
| CH-2212 | Stoos - Schwyz | Uri Rot Stock | 8.582, 46.865 |
| CH-2221 | Gotthard - Urserntal | Meiental | 8.513, 46.720 |
| CH-2222 | Oberalp | Maderanertal | 8.783, 46.776 |
| CH-2223 | Piora - Quinto | nördliches Urseren | 8.558, 46.652 |
| CH-2224 | Bedretto Süd | südliches Urseren | 8.584, 46.608 |
| CH-3111 | Sernftal - Glarnerland | Glarus Nord | 9.040, 47.108 |
| CH-3112 | Klausenpass | Glarus Süd-Grosstal | 8.991, 46.920 |
| CH-3113 | Walensee - Flumserberg | Glarus Süd-Sernftal | 9.151, 46.952 |
| CH-3114 | Muotatal Ost | Glarus Mitte | 8.970, 47.012 |
| CH-3211 | Churfirsten - Toggenburg | Appenzeller Alpen | 9.509, 47.357 |
| CH-3221 | Glarus Nord | Toggenburg | 9.010, 47.221 |
| CH-3222 | Flumserberg - Weisstannen | Alpstein - Alvier | 9.357, 47.172 |
| CH-3223 | Rheintal Süd | Flumserberg | 9.230, 47.070 |
| CH-3224 | Prättigau Nord | Sarganserland | 9.398, 46.946 |
| CH-3311 | Appenzell - Alpstein | Liechtenstein | 9.550, 47.137 |
| CH-4111 | Monthey - Champéry | Emosson | 6.950, 46.075 |
| CH-4114 | Fully - Isérables | Conthey-Fully | 7.237, 46.242 |
| CH-4121 | Conthey - Nendaz bas | Montana | 7.465, 46.311 |
| CH-4211 | Anniviers - Tourtemagne | Leukerbad - Lötschental | 7.730, 46.403 |
| CH-4213 | Baltschieder - Lötschberg | Konkordia Gebiet | 8.011, 46.466 |
| CH-4243 | oberes Goms | nördliches Obergoms | 8.336, 46.566 |
| CH-5121 | Plessur | Calanda | 9.494, 46.900 |
| CH-5124 | Vorderrhein | Flims | 9.171, 46.824 |
| CH-5211 | Flims | nördliches Tujetsch | 8.809, 46.713 |
| CH-6113 | Blenio | Bleniotal | 8.927, 46.519 |
| CH-6114 | Valle Maggia nord | obere Maggiatäler | 8.524, 46.355 |
| CH-6115 | Verzasca | untere Leventina | 8.883, 46.404 |
| CH-6121 | Bosco Gurin - Valle Maggia | untere Maggiatäler | 8.719, 46.219 |
| CH-6122 | Bellinzonese | Riviera | 8.987, 46.218 |
| CH-6131 | Centovalli - Gambarogno | Luganese | 8.921, 46.051 |
| CH-6132 | Luganese | Mendrisiotto | 8.990, 45.905 |
| CH-6211 | Mesolcina | alto Moesano | 9.207, 46.460 |
| CH-6212 | Calanca | basso Moesano | 9.210, 46.297 |
| CH-8111 | Genferseegebiet | Saint-Cergue | 6.171, 46.477 |
| CH-8112 | Waadtländer Vorland | Vallée de Joux | 6.311, 46.655 |
| CH-8113 | Freiburger Voralpen | Yverdon - Bevaix | 6.608, 46.827 |
| CH-8114 | Berner Voralpen West | Val de Travers | 6.563, 46.917 |
| CH-8211 | Bernese Mittelland West | Val de Ruz - Colombier | 6.876, 47.035 |
| CH-8212 | Emmental | Bienne - Neuchâtel | 7.073, 47.088 |
| CH-8213 | Bernese Mittelland Ost | Vallon de Saint-Imier | 7.078, 47.147 |
| CH-8214 | Solothurner Jura | Moutier - Tavannes | 7.336, 47.281 |
| CH-8215 | Basler Jura | Thal | 7.602, 47.299 |
| CH-8221 | Berner Jura | La Chaux-de-Fonds - Le Locle | 6.767, 47.096 |
| CH-8222 | Neuchâteler Jura | Franches-Montagnes | 7.008, 47.269 |
| CH-8224 | Tafeljura | Delémont - Bellelay | 7.258, 47.332 |

> **Note on language.** The CAAML `lang=en` request returns the SLF region name in the *locally-dominant* language for each region — French names for Romandie regions, German names for Deutschschweiz, Italian for Ticino. SLF does **not** translate L4 region labels. The bilingual L1/L2 (`name_native` / `name_en`) shape on `MajorRegion` / `SubRegion` should be extended to `MicroRegion` if the product wants single-language L4 labels on the map.

## Region IDs in DB with no recent SLF bulletin coverage

These 9 region IDs exist in our fixture but have no SLF bulletin in the last ~200 cycles. They cover non-mountain zones (lowland, Schwarzwald, Bodensee), where SLF doesn't issue avalanche bulletins. Likely safe to leave as-is, but worth a manual review:

- `CH-8216` Schwarzwald Süd
- `CH-8223` Jurabogen
- `CH-8225` Sundgau
- `CH-8226` Oberrhein
- `CH-8227` Hotzenwald
- `CH-8228` Klettgau
- `CH-9111` Schweizer Alpen
- `CH-9211` Mittelland
- `CH-9311` Bodensee - Hochrhein

## Resort FK drift — 32 of 91 geocoded resorts (~35%)

Bbox-only spatial check (cheap proxy for true point-in-polygon). Every resort below has a `Resort.region` FK pointing to a MicroRegion whose polygon's bounding box doesn't contain the resort's stored `(lat, lon)`. The "candidate regions" column lists the MicroRegions whose bboxes do contain the point — the correct FK is one of those, to be confirmed by true point-in-polygon test:

| Resort | Coords (lat, lon) | Current FK | Candidate regions (bbox contains point) |
|---|---|---|---|
| Adelboden | 46.492, 7.559 | CH-1224 | CH-1226, CH-1227, CH-4211 |
| Aigle | 46.318, 6.969 | CH-1111 | CH-1113, CH-1114, CH-1311, CH-9111 |
| Andermatt | 46.634, 8.595 | CH-2111 | CH-2223, CH-2224 |
| Braunwald | 46.941, 8.999 | CH-3111 | CH-3112 |
| Champéry | 46.178, 6.871 | CH-4111 | CH-1312, CH-9111 |
| Château-d'Oex | 46.475, 7.132 | CH-1113 | CH-1112, CH-9111 |
| Crans-Montana | 46.313, 7.482 | CH-4215 | CH-4121 |
| Elm | 46.918, 9.169 | CH-3111 | CH-3113 |
| Engelberg | 46.822, 8.404 | CH-2121 | CH-2122, CH-9211 |
| Flims | 46.837, 9.283 | CH-5211 | CH-5124 |
| Frutigen | 46.589, 7.646 | CH-1225 | CH-1226, CH-1231, CH-1232 |
| Grimmialp | 46.580, 7.491 | CH-1227 | CH-1228 |
| Grimsel | 46.572, 8.333 | CH-1243 | CH-1247, CH-4243 |
| Grindelwald | 46.624, 8.038 | CH-1221 | CH-1242, CH-1243 |
| Gstaad | 46.473, 7.287 | CH-1223 | CH-1222, CH-9111 |
| Göschenen | 46.668, 8.588 | CH-2111 | CH-2221, CH-2223 |
| Hasliberg | 46.740, 8.199 | CH-1234 | CH-1244 |
| Kandersteg | 46.496, 7.672 | CH-1224 | CH-1226, CH-1231, CH-1232, CH-4211 |
| Klosters | 46.877, 9.879 | CH-5123 | CH-5111, CH-5112 |
| Laax | 46.805, 9.257 | CH-5211 | CH-5124, CH-5214 |
| Lauterbrunnen | 46.594, 7.907 | CH-1222 | CH-1233, CH-1234, CH-1242 |
| Lenk | 46.458, 7.443 | CH-1225 | CH-1224 |
| Lötschental | 46.404, 7.784 | CH-1232 | CH-4211, CH-4214 |
| Meiringen | 46.728, 8.187 | CH-1234 | CH-1244 |
| Melchsee-Frutt | 46.776, 8.269 | CH-2122 | CH-1246, CH-2123 |
| Mürren | 46.561, 7.894 | CH-1228 | CH-1233, CH-1234, CH-4213 |
| Realp | 46.598, 8.503 | CH-2111 | CH-2223, CH-2224 |
| Saanen | 46.490, 7.260 | CH-1223 | CH-1112, CH-1121, CH-1222, CH-9111 |
| Schönried | 46.503, 7.286 | CH-1223 | CH-1121, CH-1222, CH-9111 |
| Sustenpass | 46.730, 8.447 | CH-1243 | CH-1246, CH-2221 |
| Wengen | 46.606, 7.921 | CH-1221 | CH-1233, CH-1234, CH-1242 |
| Zweisimmen | 46.554, 7.372 | CH-1225 | CH-1121, CH-1222, CH-1228 |

## Recommended fix path

### Phase 1 — name resync (low risk, mechanical)

1. **Patch the source CSV** at `docs/eaws_regions_ch.csv` — for each of the 85 `region_id`s above, replace the `region_name` column with the SLF current name from this audit's table. Leave the `boundary` / `centre` columns untouched (they're correct).
2. **Rebuild the L4 fixture** via `python scripts/build_regions_fixture.py` (the existing script reads the CSV and emits L4 entries to `regions/fixtures/eaws.json`).
3. **Re-derive L1/L2 geometry** via `python manage.py refresh_eaws_fixtures --commit`. Names at L1/L2 are hand-maintained and the previous audit on those was clean — this only re-derives centroids/bboxes.
4. **Reload the DB** via `python manage.py loaddata regions/fixtures/eaws.json` (in dev) or run the production deploy path.
5. **Add a regression test** that asserts each `MicroRegion.name` matches SLF's CAAML name for the same `region_id`. Run as part of a periodic check (cron) so future drifts surface as test failures.

### Phase 2 — resort FK resync (needs a one-off management command)

Bbox tests are a crude proxy. Real fix: a `shapely`-backed point-in-polygon spatial join per resort.

1. Add a `manage.py audit_resort_regions [--commit]` command that:
   - Iterates `Resort.objects.geocoded()` (existing queryset).
   - For each resort, finds the MicroRegion whose polygon truly contains `(latitude, longitude)`.
   - Reports any mismatch between that and the current FK.
   - With `--commit`, updates the FK in a single transaction. Default is read-only (per the project's management-command conventions in CLAUDE.md).
2. Run it once after Phase 1 lands; commit the resulting fixture diff via `python manage.py dump_resorts_fixture --commit`.

### Phase 3 — extend MicroRegion with bilingual names (optional)

SLF returns L4 region labels in the locally-dominant language only. If the product wants English-only L4 labels (Snowdesk is English-only pre-launch per `CLAUDE.md`), extend `regions.MicroRegion` with `name_native` / `name_en`, mirroring the L1/L2 model shape. The English translations would be hand-maintained in `docs/eaws_regions_ch.csv` as a separate column.

This is out of scope for the immediate data-correctness fix and probably warrants a separate ticket of its own.

## Linear ticket

Filed as a SNOW- ticket — see the assistant's follow-up message for the ID. Reference this audit in the description.

## Out of scope for this audit

- L1/L2 (`MajorRegion`, `SubRegion`) name verification — these are hand-maintained, not derived from SLF, and the prior `name_en` spot-check (Valais, Lower Valais) showed they're current. A separate L1/L2 audit could be done but isn't urgent.
- Non-Swiss EAWS regions — Snowdesk only ingests SLF data today.
- Bulletin / `RegionDayRating` impact — bulletins are stored by `region_id` and reference `MicroRegion` by natural key. Once names update, in-flight bulletins remain consistent; no migration needed.
