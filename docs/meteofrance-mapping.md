# MeteoFrance DPBRA → Canonical CAAML mapping spec

**Status:** draft.
**Scope:** authoritative field-by-field translation reference for
`bulletins/services/sources/meteofrance.py::normalise()` (or the equivalent
adapter once SNOW-177b lands).
**Source format:** Météo-France DPBRA XML
(`<BULLETINS_NEIGE_AVALANCHE>` root element with bulletin attributes on
the root itself — there is no inner `<BULLETIN>` wrapper despite the
plural element name).
**Target:** the canonical bulletin shape every source adapter emits.
**Evidence base:** 35 live bulletins covering every active French massif
plus 1 delegated-region redirect, captured on 2026-05-18. See
[`docs/research/meteofrance/`](research/meteofrance/) for the raw XML,
the parsed catalogue (`massifs.json`), the SAT vocabulary survey
(`sat_vocabulary.md`), and a field-coverage report (`field-coverage.md`).

Tracks SNOW-177. See also the
[data-pipeline-separation discussion](https://linear.app/hugorodgerbrown/issue/SNOW-177/add-meteofrance-data-for-french-alps)
for the wider adapter-layout context.

## 1. What changes between the feeds

The three providers we now plan to consume differ along enough axes that
they justify a shared adapter contract rather than three near-duplicate
fetchers. MeteoFrance is the most divergent of the three.

| Axis | SLF | EUREGIO (ALBINA) | MeteoFrance (DPBRA) |
|---|---|---|---|
| Format | CAAML v6 JSON | CAAML v6 JSON (+ provider extras) | Custom XML — not CAAML. Full translation required. |
| Discovery | Single paginated list endpoint `?limit&offset` | Per-`(date, region)` GET on CDN; 404 = "no bulletin" | One XML per massif from `public-api.meteofrance.fr/public/DPBRA/v1/…`; regions enumerated via `liste-massifs` |
| Auth | Public, no key | Public, no key | APIM key required (`METEOFRANCE_API_KEY`) |
| Region ID scheme | Flat `CH-XXXX` | Hierarchical `AT-07-23-02` (mixed levels in one feed) | Massif names (`@MASSIF="Chablais"`) + codes from `liste-massifs.geojson` |
| Identifier | UUID in `bulletinID` | UUID in `bulletinID` | No UUID — `@ID` is an MF-internal sequence; must be synthesised |
| Time semantics | UTC, 16:00→16:00, `Z`-tagged | Same UTC + `customData.ALBINA.mainDate` for the "named" day | Naive local time (Europe/Paris); 16:00 → 18:00 next day (26h window) |
| Update cadence | ~17:00 CET; occasional 08:00 update | ~17:00 CET; `unscheduled` re-issues | One daily 16:00 issue; re-issues flagged `@AMENDEMENT="true"` |
| Languages | en/de/fr/it | en/de/it | fr only |
| Aspects | Per-problem 8-point compass | Per-problem 8-point compass | Bulletin-wide rose (`PENTE/@NE`, `@E`…) — not per-problem |
| Elevation banding | Per-problem `lowerBound`/`upperBound` | Same | Bulletin-wide split altitude (`RISQUE/@ALTITUDE`); per-problem only in prose |
| Problem vocabulary | EAWS 5-token | EAWS 5-token (+ `customData.ALBINA.avalancheType`) | MF's own 1–9 `SitAvalTyp` codes — needs lookup |
| `avalancheSize` | Numeric attribute | Numeric attribute | Free-text prose only ("Taille 1 à 2") |
| Snowpack-stability codes | Per problem | Per problem | Absent |
| Tendency / outlook | Tendency block | Tendency block + `customData.ALBINA` extras | `RisqueJ2` numeric + prose + dated J+2 |
| Historical context | Not in feed | Not in feed | `BSH` block: 7 days of past danger, snow, weather |
| Forecast in bulletin | Not in feed | Not in feed | 3-hourly `METEO` over validity window (wind at two altitudes, ISO0, rain-snow line, weather code) |
| Snow cover | Not in feed | Not in feed | `ENNEIGEMENT`: snow depth cm at 1000/1500/2000m × N/S |
| Historical archive | Same feed | Same feed | Future-facing only — historical is PDF |

## 2. Translator scope

One DPBRA XML document = one canonical bulletin for one massif covering
one validity window. The translator is pure (no I/O, no DB):

```
xml_bytes → CanonicalBulletin
```

Discovery, retry, and persistence are the orchestrator's job (see the
adapter-layout proposal under SNOW-177).

## 3. Identifier strategy

DPBRA exposes no UUID. The synthesised `bulletin_id` must be deterministic
so re-fetches are idempotent and amended bulletins overwrite cleanly.

```
bulletin_id = "FR-{massif_code}-{validity_date}[-A{amendment_seq}]"
```

- `massif_code`: from `liste-massifs.geojson` lookup, e.g. `MAS-01`. Never
  `@MASSIF` (the display name).
- `validity_date`: `@DATEVALIDITE` truncated to date in Europe/Paris.
- `amendment_seq`: appended only when `@AMENDEMENT="true"`. Sequence
  derived from `@ID` minus 1 (first amendment = `-A1`). **Open:** confirm
  `@ID` increments on amendment vs stays at 1.

`(source, bulletin_id)` is the persistence key. The `source="meteofrance"`
column makes this globally unique even if MF and SLF ever collide on the
local part.

## 4. Timezone handling

Every `DATE*` attribute in DPBRA is naive local time in Europe/Paris. The
translator MUST localise on the way in:

```python
PARIS = ZoneInfo("Europe/Paris")

def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=PARIS).astimezone(UTC)
```

Canonical times are always UTC, matching SLF/EUREGIO. The original
local-time strings are preserved verbatim under
`provider_extras.mf.raw_local_times` for debugging.

## 5. Field-by-field map

### 5.1 Top-level

| Canonical field | DPBRA source | Transform |
|---|---|---|
| `source` | — | constant `"meteofrance"` |
| `bulletin_id` | `@ID`, `@MASSIF`, `@DATEVALIDITE`, `@AMENDEMENT` | see §3 |
| `lang` | — | constant `"fr"` |
| `issued_at` | `@DATEBULLETIN` | `_parse` |
| `valid_from` | `@DATEBULLETIN` | `_parse` |
| `valid_to` | `@DATEVALIDITE` | `_parse` |
| `published_at` | `@DATEDIFFUSION` | `_parse` |
| `next_update` | — | `None` (DPBRA doesn't publish a `nextUpdate`) |
| `unscheduled` | `@AMENDEMENT` | `value == "true"` |

### 5.2 Regions

```python
regions = [CanonicalRegionRef(
    region_id=f"FR-{massif_lookup[xml.attrib['MASSIF']]}",
    name=xml.attrib["MASSIF"],
)]
```

Each DPBRA document covers exactly one massif. Region resolution requires
the `liste-massifs.geojson` lookup table to be loaded at adapter init.

**Confirmed catalogue (2026-05-18 fetch):** 35 active massifs in three
ID bands:

- `1..23` — Alps (23 massifs, "Chablais" to "Mercantour")
- `40..41` — Corse (2 massifs)
- `64..70, 72..74` — Pyrenees (10 massifs)
- `71` — Andorre, returns `<message>` (see §7)

Full list in [`docs/research/meteofrance/massifs.json`](research/meteofrance/massifs.json).
Longest observed `@MASSIF` value is 20 characters (`"Embrunais Parpaillon"`).
**Resolves open item 3:** `SubRegion.prefix` can use `max_length=32` with
generous headroom; no migration of existing column widths is needed.

### 5.3 Danger ratings

DPBRA gives a single `<RISQUE>` element with two stacked bands separated
by `@ALTITUDE`. Translates to two canonical `DangerRating` entries:

```python
split = int(risque.attrib["ALTITUDE"])
DangerRating(
    main_value=_level_from_int(risque.attrib["RISQUE1"]),    # "low" / "moderate" / ...
    elevation=Elevation(upper_bound_m=split),
    valid_time_period="all_day",
)
DangerRating(
    main_value=_level_from_int(risque.attrib["RISQUE2"]),
    elevation=Elevation(lower_bound_m=split),
    valid_time_period="all_day",
)
```

Numeric → EAWS token lookup is identical to the EAWS standard
(1=`low`, 2=`moderate`, 3=`considerable`, 4=`high`, 5=`very_high`).

`LOC1`/`LOC2` (e.g. `<2200`, `>2200`) are decorative and not used —
`@ALTITUDE` is the source of truth.

**Elevation split is optional.** In the 2026-05-18 sample, 19 of 35
bulletins (54%) carry `@ALTITUDE`/`@RISQUE2`/`@LOC2`; the remaining 16
publish a single rating for the whole massif. The translator MUST handle
both shapes — when `@ALTITUDE` is absent, emit a single `DangerRating`
with no `Elevation` bound.

### 5.4 Avalanche problems

DPBRA collapses problems into one `<STABILITE>` block with up to two
situation-types in `<SitAvalTyp SAT1="…" SAT2="…"/>`. Each non-empty SAT
emits one canonical `AvalancheProblem`.

```python
problems = []
for slot in ("SAT1", "SAT2"):
    code = sit_aval_typ.attrib.get(slot, "").strip()
    if not code:
        continue
    problems.append(AvalancheProblem(
        problem_type=SAT_TO_EAWS[int(code)],
        aspects=_aspects_from_pente(pente),     # bulletin-wide rose
        elevation=_elevation_from_prose(texte_node)
                  or Elevation(lower_bound_m=split, upper_bound_m=None),
        avalanche_size=None,                     # not encoded as attribute
        snowpack_stability=None,                 # absent in DPBRA
        valid_time_period="all_day",
    ))
```

**Open:** `SAT_TO_EAWS` lookup table. The 2026-05-18 sample only contains
codes {2, 4, 6} — see [`sat_vocabulary.md`](research/meteofrance/sat_vocabulary.md)
for frequencies and characteristic `<TITRE>` text. **Provisional mapping
inferred from the text** (CONFIRM against MF's 2026 avalanche guide
before freezing):

| SAT code | Observed `<TITRE>` text pattern | Provisional EAWS token | Status |
|---|---|---|---|
| 1 | — (not in sample) | `new_snow` | speculative |
| 2 | "chutes de neige récente se stabilisent", "manteau hivernal en altitude" | `new_snow` | observed, text-supported |
| 3 | — (not in sample) | `persistent_weak_layers` | speculative |
| 4 | "RARES INSTABILITES VENTéE", "plaques à vent" | `wind_slab` | observed, text-supported |
| 5 | — (not in sample) | `gliding_snow` | speculative |
| 6 | "RARE COULEE HUMIDE", "DE MOINS EN MOINS DE NEIGE" | `wet_snow` | observed, text-supported |
| 7–9 | — (not in sample) | TBC | speculative |

Re-run the SAT survey against a January / February day (peak season,
likely to surface codes 1, 3, 5 and any of 7–9 that exist) before the
lookup is considered complete.

The implementation must raise (not silently fall back) on unknown SAT
codes — silent fallback would mask MF feed changes. Unknown codes go in
`provider_extras.mf.unmapped_sat` for the orchestrator to log and surface
in `PipelineRun.records_failed`.

### 5.5 Aspects

`<PENTE>` carries 8 boolean attributes. One helper, applied bulletin-wide:

```python
_ASPECTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

def _aspects_from_pente(pente: Element) -> list[str]:
    return [a for a in _ASPECTS if pente.attrib.get(a) == "true"]
```

Every problem inherits the same list — DPBRA does not distinguish per
problem.

### 5.6 Per-problem elevation (prose fallback)

DPBRA's per-problem altitude is only in prose. A bounded-regex parser
handles the common patterns; on miss, fall back to the bulletin-wide
split:

```python
_ABOVE = re.compile(r"[Aa]u-dessus de (\d{3,4})\s?m")
_BELOW = re.compile(r"[Ee]n dessous de (\d{3,4})\s?m")
_BETWEEN = re.compile(r"[Ee]ntre (\d{3,4})\s?(?:et|à)\s?(\d{3,4})\s?m")
```

Order: `_BETWEEN` → `_ABOVE` → `_BELOW` → bulletin-wide split. Match must
be inside the problem's `<TEXTE>` body, not the bulletin's general prose.

### 5.7 Snowpack structure

```python
snowpack_structure_comment = qualite_texte.text.strip()
```

DPBRA's `<QUALITE>/<TEXTE>` is a free-text description of snow quality
and cover — semantically the same as CAAML's `snowpackStructure.comment`.

### 5.8 Avalanche activity

CAAML splits this into `highlights` (short) and `comment` (long). DPBRA
gives us three useful nodes:

| Canonical field | DPBRA source |
|---|---|
| `avalanche_activity.highlights` | `CARTOUCHERISQUE/RESUME` first line (split on `\n`) |
| `avalanche_activity.comment` | `STABILITE/TEXTESANSTITRE` if present, else strip title from `STABILITE/TEXTE` |
| — (dropped) | `ACCIDENTEL` and `NATUREL` are redundant with `STABILITE/TEXTE`; preserve in `provider_extras` only |

### 5.9 Tendency

One canonical `Tendency` entry for J+2:

```python
Tendency(
    tendency_type=_evolution_from_levels(risque1_today, risque1_j2),  # "increasing" / "steady" / "decreasing"
    highlights=cartouche.find("RisqueJ2").text.strip(),
    comment=cartouche.find("CommentaireRisqueJ2").text.strip(),
    valid_time=ValidTime(
        start_time=_parse(cartouche.find("RISQUE").attrib["DATE_RISQUE_J2"]),
        end_time=_parse(cartouche.find("RISQUE").attrib["DATE_RISQUE_J2"]) + timedelta(days=1),
    ),
)
```

`_evolution_from_levels` is a 3-way comparator on the numeric danger
codes — DPBRA gives us a numeric next-day value (`RISQUEMAXIJ2`), which
SLF/EUREGIO don't, so the tendency for MF is more deterministic than for
the other two providers.

### 5.10 `provider_extras.mf`

Everything DPBRA carries that CAAML can't represent. Preserved verbatim —
the website opts in by field, the canonical layer never has to interpret
it:

```python
provider_extras = {
    "mf": {
        "bsh": _xml_to_dict(bsh),                # 7-day history block
        "weather_forecast": [...],               # validity-window METEO echeances
        "snow_cover": {                          # ENNEIGEMENT + NEIGEFRAICHE
            "date": ...,
            "snow_line_north_m": int(...LimiteNord),
            "snow_line_south_m": int(...LimiteSud),
            "depths_cm": [{"altitude_m": ..., "north": ..., "south": ...}, ...],
            "fresh_24h": [{"date": ..., "min_cm": ..., "max_cm": ...}, ...],
        },
        "j2_outlook": {
            "max_danger": int(risque.attrib["RISQUEMAXIJ2"]),
            "date": risque.attrib["DATE_RISQUE_J2"],
            "label": risque_j2.text,
            "comment": commentaire_risque_j2.text,
        },
        "images": {                              # PNG asset filenames MF publishes
            "danger": image_risque.text,
            "aspect_rose": image_pente.text,
            "snow_cover": image_enneigement.text,
            "fresh_snow": image_neige_fraiche.text,
            "weather": image_meteo.text,
            "seven_day": image_seven_day.text,
        },
        "vigilance_url": "https://vigilance.meteofrance.fr/fr",
        "mf_internal_id": int(root.attrib["ID"]),
        "amendment": root.attrib["AMENDEMENT"] == "true",
        "raw_local_times": {                     # for debugging the UTC conversion
            "issued_at": root.attrib["DATEBULLETIN"],
            "valid_to": root.attrib["DATEVALIDITE"],
            "published_at": root.attrib["DATEDIFFUSION"],
        },
    }
}
```

Naming convention: `provider_extras.mf.*` (lowercase) so we don't
accidentally collide with EUREGIO's `customData.ALBINA.*` if the two ever
sit next to each other in the same canonical bulletin (they won't, but
the namespacing is cheap).

## 6. Deliberately dropped DPBRA content

| DPBRA element | Why dropped |
|---|---|
| `?xml-stylesheet` PI | XSL reference is irrelevant to ingestion |
| `TexteMeta/EnteteGenerique`, `BasDePageGenerique`, `Partenaires` | Boilerplate — same on every bulletin |
| `TexteMeta/LienDocumentation` | Static link to the MF guide |
| `AVIS`, `VIGILANCE` (when empty) | Translator emits `None`; the consumer can opt to ignore |

## 7. Errors and validation

The translator raises `MeteoFranceTranslationError` (one exception class)
on:

- Missing required attribute (`@MASSIF`, `@DATEBULLETIN`, `@DATEVALIDITE`).
- Massif not in `liste-massifs` lookup.
- Danger level not in `1..5`.
- SAT code not in `SAT_TO_EAWS` (unless caller opts to soft-fail and stash
  in `provider_extras`).

The orchestrator catches per-bulletin and increments
`PipelineRun.records_failed`, matching the existing SLF/EUREGIO behaviour.

### 7.1 Delegated-region redirect (the Andorre case)

Some massif IDs return a non-bulletin payload:

```xml
<message><![CDATA[Pour connaître les conditions neige et avalanche sur
l'Andorre, consultez le service météorologique d'Andorre :
https://www.meteo.ad/fr/etatneige .]]></message>
```

Observed for massif `71` in the 2026-05-18 fetch. The translator MUST
detect this at the root-element check (`root.tag != "BULLETINS_NEIGE_AVALANCHE"`)
and raise a distinct `MeteoFranceDelegatedRegionError` so the orchestrator
can:

- skip the massif silently rather than counting it as a failure
- record the redirect destination once per pipeline run

These massifs must NOT be discovered every day; the orchestrator should
maintain a static "delegated regions" set seeded from the catalogue.

## 8. Out of scope for this spec

- **Render-model aggregation.** The "blank problem cards" issue lives in
  `build_render_model`, not the translator. Address separately by teaching
  the builder to fall back to canonical `avalanche_problems` when
  `customData.CH.aggregation` is absent — fixes EUREGIO and MF in one go.
- **Discovery / HTTP.** The DPBRA endpoint shape (single massif fetch URL,
  rate limits, auth header) is the orchestrator's concern.
- **Region fixtures.** Loading `liste-massifs.geojson` into `MicroRegion`
  rows is SNOW-177a.
- **Backfill.** DPBRA has no historical archive in this format; only
  forward-going daily ingestion.

## 9. Open items before this spec ships

1. **Confirm `SAT_TO_EAWS` table** against the MF 2026 avalanche guide.
   The provisional table in §5.4 covers only codes 2, 4, 6 (observed) and
   speculates on 1, 3, 5, 7–9. Re-run the SAT survey against a Jan/Feb
   peak-season day before freezing the lookup.
2. **Amendment behaviour.** Still open. The 2026-05-18 sample had zero
   `@AMENDEMENT="true"` bulletins (0/35), so it can't answer whether
   `@ID` increments on re-issue. Fetch a known amended day (mid-winter
   weather events typically trigger re-issues) and inspect.
3. ~~**Massif code scheme.**~~ **Resolved.** Catalogue captured at
   [`docs/research/meteofrance/massifs.json`](research/meteofrance/massifs.json);
   longest name is 20 chars, so `SubRegion.prefix max_length=32` is
   sufficient. No migration needed for existing column widths.
4. **One bulletin per day, or two?** The 2026-05-18 sample shows one
   `DATEBULLETIN=16:00` issue per massif, identical to the original
   single-massif probe. Confirm against a second non-consecutive day
   before treating it as a hard invariant.
