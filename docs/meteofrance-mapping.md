# MeteoFrance DPBRA → CAAML JSON mapping spec

**Status:** draft.
**Scope:** authoritative field-by-field translation reference for the
MeteoFrance source adapter — a function that converts one DPBRA XML
document into the same CAAML JSON dict shape that SLF and EUREGIO emit,
ready to feed straight into `bulletins/services/data_fetcher.py::upsert_bulletin()`.
**Source format:** Météo-France DPBRA XML
(`<BULLETINS_NEIGE_AVALANCHE>` root element with bulletin attributes on
the root itself — there is no inner `<BULLETIN>` wrapper despite the
plural element name).
**Target:** CAAML v6 JSON dict, identical in shape to the SLF and EUREGIO
payloads already stored in `Bulletin.raw_data`. Provider-specific extras
go under `customData.MF` (mirroring `customData.CH` for SLF and
`customData.ALBINA` for EUREGIO).
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
| Format | CAAML v6 JSON | CAAML v6 JSON (+ provider extras) | Custom XML — not CAAML. Translator emits CAAML v6 JSON to match the other providers. |
| Discovery | Single paginated list endpoint `?limit&offset` | Per-`(date, region)` GET on CDN; 404 = "no bulletin" | One XML per massif from `public-api.meteofrance.fr/public/DPBRA/v1/…`; regions enumerated via `liste-massifs` |
| Auth | Public, no key | Public, no key | APIM key required (`METEOFRANCE_API_KEY`) |
| Region ID scheme | Flat `CH-XXXX` | Hierarchical `AT-07-23-02` (mixed levels in one feed) | Massif names (`@MASSIF="Chablais"`) + codes from `liste-massifs.geojson` |
| Identifier | UUID in `bulletinID` | UUID in `bulletinID` | No UUID — `@ID` is an MF-internal sequence; must be synthesised |
| Time semantics | UTC, 16:00→16:00, `Z`-tagged | Same UTC + `customData.ALBINA.mainDate` for the "named" day | Naive local time (Europe/Paris); 16:00 → 18:00 next day (26h window) |
| Update cadence | ~17:00 CET; occasional 08:00 update | ~17:00 CET; `unscheduled` re-issues | One daily 16:00 issue; re-issues flagged `@AMENDEMENT="true"` |
| Languages | en/de/fr/it | en/de/it | fr only |
| Aspects | Per-problem 8-point compass | Per-problem 8-point compass | Bulletin-wide rose (`PENTE/@NE`, `@E`…) — not per-problem |
| Elevation banding | Per-problem `lowerBound`/`upperBound` | Same | Bulletin-wide split altitude (`RISQUE/@ALTITUDE`); per-problem only in prose |
| Problem vocabulary | EAWS 5-token | EAWS 5-token (+ `customData.ALBINA.avalancheType`) | MF's own 1–6 `SitAvalTyp` codes (5 problems + `no_distinct`) — lookup verified, see §5.4 |
| `avalancheSize` | Numeric attribute | Numeric attribute | Free-text prose only ("Taille 1 à 2") |
| Snowpack-stability codes | Per problem | Per problem | Absent |
| Tendency / outlook | Tendency block | Tendency block + `customData.ALBINA` extras | `RisqueJ2` numeric + prose + dated J+2 |
| Historical context | Not in feed | Not in feed | `BSH` block: 7 days of past danger, snow, weather |
| Forecast in bulletin | Not in feed | Not in feed | 3-hourly `METEO` over validity window (wind at two altitudes, ISO0, rain-snow line, weather code) |
| Snow cover | Not in feed | Not in feed | `ENNEIGEMENT`: snow depth cm at 1000/1500/2000m × N/S |
| Historical archive | Same feed | Same feed | Future-facing only — historical is PDF |

## 2. Translator scope

One DPBRA XML document = one CAAML JSON bulletin dict for one massif
covering one validity window. The translator is pure (no I/O, no DB):

```python
def to_caaml(xml_bytes: bytes) -> dict[str, Any]: ...
```

The returned dict has the same top-level keys as the SLF API response —
`bulletinID`, `validTime`, `publicationTime`, `nextUpdate`, `lang`,
`unscheduled`, `regions`, `dangerRatings`, `avalancheProblems`,
`snowpackStructure`, `avalancheActivity`, `tendency`, `customData`.
Feeding it into `upsert_bulletin(raw, run)` requires no other changes;
the existing GeoJSON-envelope wrapping, region linkage, and render-model
build all work unchanged.

Discovery, retry, and persistence are the orchestrator's job — see the
follow-up ticket for HTTP/scheduling work.

## 3. Identifier strategy

DPBRA exposes no UUID. The synthesised `bulletinID` is deterministic so
re-fetches are idempotent and amended bulletins overwrite cleanly:

```
bulletinID = "FR-{NN}-{validity_date}[-A{amendment_seq}]"
```

- `{NN}`: the MF integer massif ID zero-padded to two digits,
  matching the EAWS `FR-NN` region ID (1:1 mapping established by
  SNOW-179 — see `regions/management/commands/build_france_fixture.py`).
  Example: massif `Chablais` (`@ID="1"`) → `FR-01`.
- `validity_date`: `@DATEVALIDITE` truncated to date in Europe/Paris.
- `amendment_seq`: appended only when `@AMENDEMENT="true"`. Open
  question (item 2 in §9) — confirm whether `@ID` increments on
  amendment so we can derive the suffix.

`bulletinID` is globally unique by construction — the `FR-` prefix
separates MF from SLF (`CH-`) and EUREGIO (`AT-…`, `IT-…`).

## 4. Timezone handling

Every `DATE*` attribute in DPBRA is naive local time in Europe/Paris. The
translator MUST localise on the way in and emit ISO-8601 UTC strings
ending in `Z` — exactly the format SLF emits, exactly what
`_parse_dt()` in `data_fetcher.py` expects:

```python
PARIS = ZoneInfo("Europe/Paris")

def _parse(value: str) -> str:
    """DPBRA local-time string → CAAML JSON UTC string."""
    dt = datetime.fromisoformat(value).replace(tzinfo=PARIS).astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")
```

The original local-time strings are preserved verbatim under
`customData.MF.rawLocalTimes` for debugging.

## 5. Field-by-field map

### 5.1 Top-level CAAML JSON

| CAAML key | DPBRA source | Transform |
|---|---|---|
| `bulletinID` | `@ID`, `@DATEVALIDITE`, `@AMENDEMENT` | see §3 |
| `lang` | — | constant `"fr"` |
| `validTime.startTime` | `@DATEBULLETIN` | `_parse` |
| `validTime.endTime` | `@DATEVALIDITE` | `_parse` |
| `publicationTime` | `@DATEDIFFUSION` | `_parse` |
| `nextUpdate` | — | omit key (DPBRA publishes one daily, no `nextUpdate` semantic) |
| `unscheduled` | `@AMENDEMENT` | `xml.attrib.get("AMENDEMENT") == "true"` |

### 5.2 Regions

```python
massif_id = int(xml.attrib["ID"])
caaml["regions"] = [{
    "regionID": f"FR-{massif_id:02d}",
    "name": xml.attrib["MASSIF"],
}]
```

Each DPBRA document covers exactly one massif. The `FR-NN` ↔ MF integer
mapping is 1:1 and was established by **SNOW-179**: massif `Chablais`
(`@ID="1"`) maps to EAWS region `FR-01`, `Mercantour` (`@ID="23"`) to
`FR-23`, `Cinto-Rotondo` (`@ID="40"`) to `FR-40`, and so on. All 35
active massif IDs are already present in `regions/fixtures/eaws_FR.json`
as `MicroRegion` rows, so `upsert_bulletin`'s `_get_region` lookup will
resolve them without further fixture work.

**Confirmed catalogue (2026-05-18 fetch):** 35 active massifs in three
ID bands:

- `1..23` — Alps (23 massifs, "Chablais" to "Mercantour")
- `40..41` — Corse (2 massifs)
- `64..70, 72..74` — Pyrenees (10 massifs)
- `71` — Andorre, returns `<message>` (see §7)

Full list in [`docs/research/meteofrance/massifs.json`](research/meteofrance/massifs.json).

### 5.3 Danger ratings (`dangerRatings`)

DPBRA gives a single `<RISQUE>` element with up to two stacked bands
separated by `@ALTITUDE`. Translates to one or two CAAML `dangerRatings`
entries:

```python
# Two-band case (54% of the 2026-05-18 sample)
split = int(risque.attrib["ALTITUDE"])
caaml["dangerRatings"] = [
    {
        "mainValue": _LEVEL[int(risque.attrib["RISQUE1"])],   # "low" / "moderate" / …
        "elevation": {"upperBound": str(split)},
        "validTimePeriod": "all_day",
    },
    {
        "mainValue": _LEVEL[int(risque.attrib["RISQUE2"])],
        "elevation": {"lowerBound": str(split)},
        "validTimePeriod": "all_day",
    },
]

# Single-band case (46%) — no @ALTITUDE / @RISQUE2
caaml["dangerRatings"] = [{
    "mainValue": _LEVEL[int(risque.attrib["RISQUE1"])],
    "validTimePeriod": "all_day",
}]
```

`_LEVEL` is the EAWS mapping shared with SLF/EUREGIO
(1=`low`, 2=`moderate`, 3=`considerable`, 4=`high`, 5=`very_high`).

`LOC1`/`LOC2` (e.g. `<2200`, `>2200`) are decorative — `@ALTITUDE` is the
source of truth.

### 5.4 Avalanche problems (`avalancheProblems`)

DPBRA collapses problems into one `<STABILITE>` block with up to two
situation-types in `<SitAvalTyp SAT1="…" SAT2="…"/>`. Each non-empty SAT
emits one CAAML `avalancheProblems` entry:

```python
problems = []
for slot in ("SAT1", "SAT2"):
    code = sit_aval_typ.attrib.get(slot, "").strip()
    if not code:
        continue
    problem = {
        "problemType": SAT_TO_EAWS[int(code)],
        "aspects": _aspects_from_pente(pente),           # CAAML 8-letter compass list
        "validTimePeriod": "all_day",
    }
    elev = _elevation_from_prose(texte_node)
    if elev:
        problem["elevation"] = elev                       # {"upperBound": "2400"} etc.
    problems.append(problem)
caaml["avalancheProblems"] = problems
```

DPBRA encodes neither `avalancheSize` (only free-text "Taille 1 à 2" in
prose) nor `snowpackStability` — both keys are omitted from the emitted
problem dict, matching how EUREGIO handles partial data.

**`SAT_TO_EAWS` lookup (verified against the MF avalanche guide 2025):**

| SAT code | MF label | EAWS token |
|---|---|---|
| 1 | Neige fraîche | `new_snow` |
| 2 | Neige ventée | `wind_slab` |
| 3 | Couche fragile persistante | `persistent_weak_layers` |
| 4 | Neige humide | `wet_snow` |
| 5 | Avalanches de fond | `gliding_snow` |
| 6 | Aucune situation avalancheuse typique prédominante | `no_distinct_avalanche_problem` |

Source: `guide-avalanche-2025-meteo-france.pdf` p.13 — the section
"Situations avalancheuses typiques" lists the six categories in this
order. The numeric code is the 1-based position in the list. Empirically
confirmed against the 2026-05-18 sample: bulletins with `SAT1=2` carry
wind-slab text ("plaques à vent"), `SAT1=4` carry wet-snow text ("neige
récente peut couler ... réchauffement diurne"), and `SAT1=6` are
late-season massifs with no dominant problem.

Cross-check during MF translator implementation: when ingesting a
peak-season day for the first time, raise on any SAT code outside
`{1..6}` so an MF feed extension doesn't ship silently. EAWS publishes a
5-problem set plus `no_distinct`; the table above covers all six MF
positions exactly.

The implementation MUST raise (not silently fall back) on unknown SAT
codes — silent fallback would mask MF feed changes. The orchestrator
catches and increments `PipelineRun.records_failed`, matching SLF/EUREGIO
behaviour.

### 5.5 Aspects

`<PENTE>` carries 8 boolean attributes. One helper, applied bulletin-wide:

```python
_ASPECTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

def _aspects_from_pente(pente: Element) -> list[str]:
    return [a for a in _ASPECTS if pente.attrib.get(a) == "true"]
```

The returned list goes into each problem's `aspects` key. Every problem
inherits the same rose — DPBRA does not distinguish per problem.

### 5.6 Per-problem elevation (prose fallback)

DPBRA's per-problem altitude is only in prose. A bounded-regex parser
handles the common patterns and emits a CAAML `elevation` object
(`{"upperBound": "2400"}`, `{"lowerBound": "2400"}`, or both); on miss,
fall back to the bulletin-wide split:

```python
_ABOVE = re.compile(r"[Aa]u-dessus de (\d{3,4})\s?m")
_BELOW = re.compile(r"[Ee]n dessous de (\d{3,4})\s?m")
_BETWEEN = re.compile(r"[Ee]ntre (\d{3,4})\s?(?:et|à)\s?(\d{3,4})\s?m")
```

Order: `_BETWEEN` → `_ABOVE` → `_BELOW` → bulletin-wide split. Match must
be inside the problem's `<TEXTE>` body, not the bulletin's general prose.

### 5.7 Snowpack structure (`snowpackStructure`)

```python
caaml["snowpackStructure"] = {
    "comment": qualite_texte.text.strip(),
}
```

DPBRA's `<QUALITE>/<TEXTE>` is a free-text description of snow quality
and cover — semantically the same as CAAML's `snowpackStructure.comment`.

### 5.8 Avalanche activity (`avalancheActivity`)

| CAAML key | DPBRA source |
|---|---|
| `avalancheActivity.highlights` | `CARTOUCHERISQUE/RESUME` first line (split on `\n`) |
| `avalancheActivity.comment` | `STABILITE/TEXTESANSTITRE` if present, else strip the title from `STABILITE/TEXTE` |

`CARTOUCHERISQUE/ACCIDENTEL` and `CARTOUCHERISQUE/NATUREL` are redundant
with `STABILITE/TEXTE`; preserve them under `customData.MF` only (see §5.10).

### 5.9 Tendency (`tendency`)

One CAAML `tendency` entry for J+2:

```python
caaml["tendency"] = [{
    "tendencyType": _evolution_from_levels(risque1_today, risque1_j2),  # "increasing" / "steady" / "decreasing"
    "highlights": cartouche.find("RisqueJ2").text.strip(),
    "comment": cartouche.find("CommentaireRisqueJ2").text.strip(),
    "validTime": {
        "startTime": _parse(date_risque_j2),
        "endTime": _parse_plus_one_day(date_risque_j2),
    },
}]
```

`_evolution_from_levels` is a 3-way comparator on the numeric danger
codes — DPBRA gives us a numeric next-day value (`RISQUEMAXIJ2`), which
SLF/EUREGIO don't, so the tendency for MF is more deterministic than for
the other two providers.

### 5.10 `customData.MF`

Everything DPBRA carries that CAAML can't represent goes here, mirroring
the `customData.CH` (SLF) and `customData.ALBINA` (EUREGIO) conventions.
The render-model builder reads keys from this namespace when an
MF-specific field is needed; nothing here is required for the basic
calendar / map render:

```python
caaml["customData"] = {
    "MF": {
        "bsh": _xml_to_dict(bsh),                # 7-day history block
        "weatherForecast": [...],                # validity-window METEO echeances
        "snowCover": {                           # ENNEIGEMENT + NEIGEFRAICHE
            "date": ...,
            "snowLineNorthM": int(...LimiteNord),
            "snowLineSouthM": int(...LimiteSud),
            "depthsCm": [{"altitudeM": ..., "north": ..., "south": ...}, ...],
            "fresh24h": [{"date": ..., "minCm": ..., "maxCm": ...}, ...],
        },
        "j2Outlook": {
            "maxDanger": int(risque.attrib["RISQUEMAXIJ2"]),
            "date": risque.attrib["DATE_RISQUE_J2"],
            "label": risque_j2.text,
            "comment": commentaire_risque_j2.text,
        },
        "images": {                              # PNG asset filenames MF publishes
            "danger": image_risque.text,
            "aspectRose": image_pente.text,
            "snowCover": image_enneigement.text,
            "freshSnow": image_neige_fraiche.text,
            "weather": image_meteo.text,
            "sevenDay": image_seven_day.text,
        },
        "vigilanceUrl": "https://vigilance.meteofrance.fr/fr",
        "mfInternalId": int(root.attrib["ID"]),
        "amendment": root.attrib["AMENDEMENT"] == "true",
        "rawLocalTimes": {                       # for debugging the UTC conversion
            "issuedAt": root.attrib["DATEBULLETIN"],
            "validTo": root.attrib["DATEVALIDITE"],
            "publishedAt": root.attrib["DATEDIFFUSION"],
        },
        "redundantProse": {                      # preserved for completeness; not used by render
            "accidentel": cartouche.find("ACCIDENTEL").text,
            "naturel": cartouche.find("NATUREL").text,
        },
    },
}
```

camelCase keys mirror the surrounding CAAML JSON convention. The `MF`
namespace is unique (SLF uses `CH`, EUREGIO uses `ALBINA` and
`LWD_Tyrol`) — no collision risk.

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

- Missing required attribute (`@ID`, `@MASSIF`, `@DATEBULLETIN`, `@DATEVALIDITE`).
- `@ID` not in the SNOW-179 catalogue (i.e. unknown massif).
- Danger level not in `1..5`.
- SAT code not in `SAT_TO_EAWS` (the {1..6} keys defined in §5.4).

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
  the builder to fall back to top-level `avalancheProblems` when
  `customData.CH.aggregation` is absent — fixes EUREGIO and MF in one go.
- **Discovery / HTTP.** The DPBRA endpoint shape (single massif fetch URL,
  rate limits, auth header) is the orchestrator's concern.
- **Region fixtures.** Already loaded by **SNOW-179**
  (`regions/fixtures/eaws_FR.json` — 35 `MicroRegion` rows, EAWS `FR-NN`
  IDs 1:1 with MF integer massif codes).
- **Backfill.** DPBRA has no historical archive in this *XML* format;
  the live API is forward-going only. Pre-2026 data is available as
  PDF from MF's `donneespubliques` portal, and a community project
  (see §10) has already scraped that archive into CSV. If/when
  Snowdesk wants historical French danger ratings, that's the source
  to integrate — separately ticketed.

## 10. Related projects

### Historical backfill — multi-coop/meteofrance_bra_hist (GitLab)

[`gitlab.com/multi-coop/meteofrance_bra_hist`](https://gitlab.com/multi-coop/meteofrance_bra_hist)
(MIT) scrapes the historical BRA PDFs MF publishes at
`donneespubliques.meteofrance.fr` and republishes them as one CSV per
massif at `data/<MASSIF>/hist.csv`. Coverage runs from at least
2018-12-17 and is kept current. The CSV schema captures `risque1`,
`risque2`, altitude split, the bulletin's free-text comment, the
download URL of the source PDF, and weather snapshots at 00 h / 06 h /
12 h (sky, isotherms, wind at two altitudes, rain-snow line).

**Useful for:** historical backfill of French danger ratings into
Snowdesk's `Bulletin`/`RegionDayRating` tables — saves writing a PDF
scraper of our own. Track as a follow-up SNOW ticket once forward-going
ingestion (SNOW-177b) is in production.

**Not useful for** the remaining open items in this spec:

- The project's CSV does not contain `SitAvalTyp` (SAT) codes. Their
  ROADMAP issues #60 and #61 cover adding SAT extraction; until then
  the SAT vocabulary completeness gap can only be closed via a
  peak-season live-XML fetch.
- The CSV does not preserve XML-level attributes like `@AMENDEMENT`
  or `@ID`, so it can't answer the amendment-behaviour question
  either.

### Cross-validation during shadow-mode

If the orchestrator runs DPBRA ingestion in shadow mode before
go-live, comparing our `bulletins/services/sources/meteofrance.py`
output against the same `(date, massif)` row in
`meteofrance_bra_hist`'s CSV catches translation bugs the unit-test
fixtures may miss — particularly around the elevation-split semantics
and `evolurisque` evolution arrows. Low-cost validation; high signal.

## 9. Open items before this spec ships

1. ~~**Confirm `SAT_TO_EAWS` table.**~~ **Resolved.** The lookup in §5.4
   is verified against `guide-avalanche-2025-meteo-france.pdf` p.13.
   Empirically aligned with the 2026-05-18 sample for codes {2, 4, 6}.
2. **Amendment behaviour.** Still open. The 2026-05-18 sample had zero
   `@AMENDEMENT="true"` bulletins (0/35), and the avalanche guide doesn't
   document the attribute (it's part of the API contract, not the reader
   guide). Two paths: (a) request MF's DPBRA schema documentation via the
   APIM portal, or (b) catch the first amended bulletin during SNOW-177b
   shadow-mode ingestion — implementation must log `(@ID, @AMENDEMENT)`
   so we can disambiguate when it occurs.
3. ~~**Massif code scheme.**~~ **Resolved by SNOW-179.** All 35 massifs
   are already loaded as `MicroRegion` rows in
   `regions/fixtures/eaws_FR.json`, with `FR-NN` ↔ MF integer ID 1:1
   (`int("FR-68".split("-")[1]) == 68`). Catalogue cross-reference at
   [`docs/research/meteofrance/massifs.json`](research/meteofrance/massifs.json).
   No further fixture or migration work required for ingestion.
4. ~~**One bulletin per day, or two?**~~ **Resolved.** Guide p.9 (line
   323-324): "Des bulletins quotidiens […] rédigés vers 16 h, ce sont des
   prévisions jusqu'au lendemain soir." Strictly one issue per massif per
   day, 16:00 Europe/Paris. The "twice daily at 6h and 16h" on p.15 refers
   to the *vigilance* map (separate product), not the BRA.
