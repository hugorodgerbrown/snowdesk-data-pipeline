# Météo-France BRA Backfill Pipeline

> **DECOMMISSIONING WARNING** — `donneespubliques.meteofrance.fr` is shutting
> down. The archive at `https://donneespubliques.meteofrance.fr/` will stop
> serving historical PDFs once the new Confluence portal is live. Run the full
> pipeline before the host goes offline. After that point only the committed
> fixture PDFs will be available.

This directory contains a four-stage one-shot pipeline for backfilling the
Météo-France BRA (Bulletin de Risque d'Avalanche) archive for the 2023–2026
Alpine seasons. Output is a single NDJSON file of CAAML 6.0 GeoJSON Feature
records, one per bulletin, ready for ingestion by the Snowdesk Django pipeline.

---

## Quick-start: full run sequence

```bash
# Prerequisites:
#   poetry install                 (includes pdfplumber)
#   brew install aria2             (macOS; see below for alternatives)

cd scripts/meteofrance-archive/

# Stage 1 — fetch daily index responses (2025-11-01 → today, Alpine only)
poetry run python mf_bra_index_archive.py --commit
# Output: bra_indexes.ndjson

# Stage 2 — expand indexes into per-bulletin URLs
poetry run python mf_bra_url_discover.py --commit
# Output: bra_urls.csv

# Stage 3 — build the aria2c download manifest
poetry run python build_aria2c_input.py --commit
# Output: bra_downloads.txt
# Exit code 2 if any rows were skipped (see below).

# Stage 4 — download PDFs (system binary, not a Python script)
mkdir -p bra_pdfs
aria2c --input-file=bra_downloads.txt --dir=bra_pdfs --max-concurrent-downloads=4

# Stage 5 — parse PDFs into CAAML 6.0 NDJSON
poetry run python mf_bra_to_caaml.py --input bra_pdfs/ --output bulletins.ndjson
# Output: bulletins.ndjson
```

All scripts default to **dry-run / read-only**. Pass `--commit` (Stages 1–3)
or omit `--dry-run` (Stage 5) to write output.

---

## Scripts

### Stage 1 — `mf_bra_index_archive.py`

Fetches the daily BRA index JSON from the Météo-France public API
(`https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA/bra.YYYYMMDD.json`)
for each day in a date range, and writes one NDJSON record per date:

```json
{"date": "2026-01-15", "status": "ok", "massifs": [{"massif": "CHABLAIS", "heures": ["20260115100000"]}]}
{"date": "2026-07-01", "status": "not_found", "massifs": []}
```

**Resumable:** dates already present in `bra_indexes.ndjson` are skipped.
Re-run after a network failure and it continues from where it left off.

**Season filter:** only Nov–Apr dates are fetched (no bulletins in summer).

**Rate limiting:** 0.2 s between requests; custom `User-Agent` header.

```bash
# Defaults: 2025-11-01 → today, writes bra_indexes.ndjson with --commit
python mf_bra_index_archive.py --commit
python mf_bra_index_archive.py --start 2023-11-01 --end 2024-04-30 --commit
python mf_bra_index_archive.py  # dry-run: prints what would be fetched
```

### Stage 2 — `mf_bra_url_discover.py`

Reads `bra_indexes.ndjson` and expands each `(massif, heures[])` pair into
one CSV row in `bra_urls.csv`:

```
massif,date,heures,url,status
CHABLAIS,2026-01-15,20260115100000,https://donneespubliques.meteofrance.fr/.../BRA.CHABLAIS.20260115100000.pdf,ok
```

Multiple `heures` per (massif, date) are preserved. When aria2c downloads
both and the same normalised filename is used for both, the later download
overwrites the earlier one on disk — this is the intended behaviour for
re-published bulletins.

**Massif filter:** defaults to the 23 Alpine massifs. Pass
`--massif CHABLAIS MONT-BLANC ...` to restrict.

```bash
python mf_bra_url_discover.py --commit
python mf_bra_url_discover.py --massif CHABLAIS MONT-BLANC --commit
```

### Stage 3 — `build_aria2c_input.py`

Reads `bra_urls.csv` and writes `bra_downloads.txt` in aria2c input file
format:

```
https://donneespubliques.meteofrance.fr/.../BRA.CHABLAIS.20260115100000.pdf
  out=BRA.CHABLAIS.2026-01-15.pdf

```

The `--out` directive normalises the filename to `BRA.{MASSIF}.{YYYY-MM-DD}.pdf`,
stripping the `heures` timestamp from the URL. This ensures all bulletins for a
given (massif, date) land on the same file path, and the last download wins.

**Exit code 2** if any rows are skipped (missing massif/url). Treat this as a
warning, not a fatal error — the skipped rows are logged to stderr.

```bash
python build_aria2c_input.py --commit
python build_aria2c_input.py  # dry-run: prints what would be written
```

### Stage 4 — `aria2c` (system binary)

Not a Python script. Install via:

```bash
# macOS
brew install aria2

# Debian / Ubuntu
apt-get install aria2

# From source: https://aria2.github.io/
```

Typical invocation:

```bash
mkdir -p bra_pdfs
aria2c \
  --input-file=bra_downloads.txt \
  --dir=bra_pdfs \
  --max-concurrent-downloads=4 \
  --max-connection-per-server=2 \
  --split=2 \
  --user-agent="Snowdesk-archive/1.0 (+https://github.com/your-org/snowdesk)"
```

**Be a good citizen:** `--max-concurrent-downloads=4` is reasonable. Don't
hammer the public API.

### Stage 5 — `mf_bra_to_caaml.py`

Parses all `BRA.*.pdf` files in an input directory and writes one NDJSON line
per bulletin to `bulletins.ndjson`. Each record is a CAAML 6.0 GeoJSON Feature:

```json
{
  "type": "Feature",
  "geometry": null,
  "properties": {
    "lang": "fr",
    "regions": [{"regionID": "FR-CHABLAIS", "name": "Chablais"}],
    "validTime": {"startTime": "2026-01-15T00:00:00+00:00", "endTime": "2026-01-15T23:59:59+00:00"},
    "dangerRatings": [{"mainValue": "low", "elevation": {"lowerBound": null, "upperBound": null}, "valid": true}],
    "highlights": "MANTEAU NEIGEUX STABLE.",
    "avalancheActivity": {"highlights": "...", "comment": "..."},
    "snowpackStructure": {"comment": "..."},
    "avalancheProblems": [{"problemType": "wet_snow", "satLabel": "neige humide", "order": 1, "aspects": [], "elevation": {}}],
    "tendency": [{"dangerRating": "low", "tendencyType": "steady", "comment": "..."}],
    "weather": {
      "comment": "...",
      "isotherm0": [4000, 4000, 4000, 4100],
      "wind": [{"altitude": "2000m", "speeds": [5, 0, 0, 5]}, {"altitude": "2500m", "speeds": [10, 5, 5, 10]}]
    },
    "customData": {
      "MF": {
        "massif": "CHABLAIS",
        "date": "2026-01-15",
        "typicalAvalancheSituations": ["neige humide"],
        "source_file": "BRA.CHABLAIS.20260115100000.pdf"
      }
    }
  }
}
```

```bash
# Dry-run: prints field-coverage report across all PDFs (does not write output)
python mf_bra_to_caaml.py --input bra_pdfs/ --dry-run

# Write output (resumes from any existing file by default)
python mf_bra_to_caaml.py --input bra_pdfs/ --output bulletins.ndjson

# Force a fresh parse, overwriting any existing output
python mf_bra_to_caaml.py --input bra_pdfs/ --output bulletins.ndjson --no-resume
```

Output is written incrementally: each bulletin is appended and flushed
before the next PDF is opened, so an interrupted run leaves a valid
NDJSON file on disk that the next invocation will resume from.

---

## Resumability

- **Stage 1** is resumable: dates already in `bra_indexes.ndjson` are skipped
  on re-run. Safe to interrupt and restart.
- **Stages 2–3** are idempotent: re-running overwrites the output file.
- **Stage 4 (aria2c)**: aria2c writes `.aria2` control files alongside each
  PDF. Interrupted downloads resume from the last byte.
- **Stage 5** is resumable and idempotent. Envelopes are streamed to the
  output file one line at a time with an explicit flush after each, so a
  killed process leaves a valid NDJSON file behind. Re-running reads the
  existing file, builds the set of PDFs already represented (keyed by
  `customData.MF.source_file`), and skips them. If the previous run was
  killed mid-write and the tail of the file is a partial line, that line
  is truncated at the last clean boundary before appending. Pass
  `--no-resume` to force a fresh parse over the whole input directory.

---

## Output artefacts

The following files are listed in `.gitignore` and must **not** be committed:

| File | Description |
|------|-------------|
| `bra_indexes.ndjson` | Raw daily index responses (can be 100+ lines per season) |
| `bra_urls.csv` | Expanded URL list (one row per bulletin) |
| `bra_downloads.txt` | aria2c input file |
| `bulletins.ndjson` | Final CAAML NDJSON output |
| `bra_pdfs/*.pdf` | Downloaded PDFs (~1 MB each × 23 massifs × ~150 days) |

The PDF corpus can reach ~3 GB for a full season. Store long-term artefacts in
an object store, not git.

---

## Known limitations

1. **Per-day historical danger ratings (page 2)** are not parsed. The page-2
   chart grid is position-sensitive and varies across massifs. Only the
   current-day rating from page 1 is extracted.

2. **Snow-depth and cover series (page 2)** are partially extracted. Text
   rendering in the chart area is fragmented and missing values are common.

3. **Trailing `None` in last-day fresh snow** — when the value is 0 the chart
   sometimes omits the label entirely. The missing value is left as `None`
   rather than assumed to be 0.

4. **SAT → problem type mapping** (`_sat_mapping.py`) is an initial reasonable
   set. The official Météo-France → EAWS alignment has not been verified and is
   tracked as a follow-up ticket.

5. **Wind direction** is not present in the BRA PDF (only speed is given).
   The `speeds` array contains four time-slot values (nuit/matin/après-midi/soir).

6. **Avalanche problem elevation/aspect** is extracted from free-text narrative
   (best-effort). Structured per-problem elevation and aspect data are not
   reliably available in BRA PDFs.

7. **Layout drift**: The column-crop constants (`COLUMN_SPLIT_X`, band
   definitions in `_pdf_extract.py`) are calibrated against the 2025–2026
   layout. If Météo-France change the PDF template for older seasons, some
   fields may parse incorrectly. The `--dry-run` field-coverage report is the
   first tool to reach for when debugging.

---

## Running tests

```bash
# All tests (includes PDF parser integration tests using committed fixtures)
poetry run tox -e test

# Just the meteofrance archive tests (faster)
poetry run pytest tests/scripts/meteofrance_archive/ -q

# Smoke-test the parser against committed fixtures
poetry run python scripts/meteofrance-archive/mf_bra_to_caaml.py \
  --input tests/scripts/meteofrance_archive/fixtures/ \
  --dry-run
```

The integration tests use two committed PDF fixtures:

- `BRA.CHABLAIS.20260521140706.pdf` — danger=1 (low), single SAT label, single
  elevation band.
- `BRA.MONT-BLANC.20260521140702.pdf` — danger=2 above 3600m / danger=1 below,
  two SAT labels (neige ventée + neige humide).

These act as both regression guard and executable acceptance tests.
