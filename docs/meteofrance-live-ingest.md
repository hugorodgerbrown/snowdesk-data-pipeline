# Météo-France live ingest — operations guide

**Ticket:** SNOW-259
**Status:** Production-ready as of this document (SNOW-259).

This document covers the daily ingest contract, Render deployment
requirements, idempotency behaviour, and offline-testing workflow for the
Météo-France DPBRA bulletin source.

---

## Daily ingest contract

### Scheduled command

```
fetch_bulletins --source meteofrance --commit
```

This is part of the combined bulletin-ingestion job in
[`schedule.py`](../schedule.py):

```
fetch_bulletins --source slf albina meteofrance --commit
```

Cadence: every hour at :00 and :05 UTC (`0,5 * * * *`), driven by the
`snowdesk-scheduler` Background Worker. See
[`docs/management-commands.md`](management-commands.md#operational-requirements)
for the full operational-requirements table.

### Massifs fetched

35 active massifs are covered on every run, in three bands:

| Band | IDs | Count | Description |
|------|-----|-------|-------------|
| Alps | 1–23 | 23 | Chablais → Mercantour |
| Corse | 40, 41 | 2 | Cinto-Rotondo, Renoso |
| Pyrenees | 64–70, 72–74 | 10 | Néouvielle → Haute-Ariège, Andorre excluded |

Full list: [`docs/research/meteofrance/massifs.json`](research/meteofrance/massifs.json).

### Delegated-region skips

Massif 71 (Andorre/Andorra) returns a delegation redirect XML
(`<message>…</message>`) rather than a bulletin. The fetcher detects
this via a root-element check and raises
`MeteoFranceDelegatedRegionError`, which is caught and counted in the
`delegated` bucket — **not** in `records_failed`. The massif is excluded
from `METEOFRANCE_MASSIF_IDS` in `config/settings/base.py` so the loop
never hits it in a standard run; the exception path exists as a safeguard
for future delegations.

### Expected cadence

Météo-France publishes one BRA per massif per day at approximately
16:00 Europe/Paris. The DPBRA API serves only today's live bulletin per
massif; there is no date-range parameter. For historical data, see the
[archive ingest path](../scripts/meteofrance-archive/README.md).

---

## API key requirement

The DPBRA APIM requires an `apikey` header on every request:

```
GET https://public-api.meteofrance.fr/public/DPBRA/v1/massif/{id}/BRA
apikey: <METEOFRANCE_API_KEY>
```

Without a valid key, `fetch_meteofrance_bulletin` raises `RuntimeError`
immediately and the massif is counted as a failure.

---

## Render deployment — env var wiring

### `render.yaml` declaration

`METEOFRANCE_API_KEY` is declared in both service blocks that need it:

```yaml
# snowdesk-website (web service)
envVars:
  - key: METEOFRANCE_API_KEY
    sync: false
  …

# snowdesk-scheduler (background worker)
envVars:
  - key: METEOFRANCE_API_KEY
    sync: false
  …
```

### Blueprint auto-sync is disabled

The file header in [`render.yaml`](../render.yaml) documents this
explicitly: Blueprint auto-sync is **not** enabled. Updating `render.yaml`
records the intent in the repo but does **not** propagate to the live
deployment automatically.

### Operator action required after merge

After this file is merged, you must manually set the env var in the Render
dashboard for the `snowdesk-scheduler` service:

1. Log in to the [Render dashboard](https://dashboard.render.com).
2. Navigate to **snowdesk-scheduler** → **Environment**.
3. Add (or confirm) the key `METEOFRANCE_API_KEY` with the same value
   already set on `snowdesk-website`.
4. Save. Render restarts the worker automatically.

Until step 4 is done, every scheduled `fetch_bulletins --source meteofrance`
invocation in the worker will raise `RuntimeError` for every massif and log
35 failures per run.

---

## Idempotency

Re-running the pipeline for the same day with no `--force` flag is a
no-op:

- The fetcher calls `upsert_bulletin()`, which checks whether a
  `Bulletin` with the same `bulletin_id` already exists.
- If it does (and `force=False`), the massif is counted as `skipped`
  and no DB write occurs.
- A second `--commit` run over the same day therefore produces
  `records_created == 0` and `records_updated == 0`.

To force a re-fetch (e.g. after a translation-logic fix):

```bash
poetry run python manage.py fetch_bulletins --source meteofrance --commit --force
```

---

## Offline testing — `--local-mirror`

The `--local-mirror` flag points the fetcher at a local file-system
directory instead of the live APIM, so no API key is needed and no
network I/O occurs:

```bash
# Configure the mirror in .env (or development.py):
METEOFRANCE_API_LOCAL_MIRROR_URL=file:///path/to/bulletins-2026-05-18

# Run with local mirror (read-only):
poetry run python manage.py fetch_bulletins --source meteofrance --local-mirror

# Run with local mirror and persist:
poetry run python manage.py fetch_bulletins --source meteofrance --local-mirror --commit
```

The bundled research fixtures in
`docs/research/meteofrance/bulletins-2026-05-18/` cover 35 massifs and
include the delegated-region file (`massif-071.xml`). They are the
reference inputs for the fetcher regression tests.

To point the dev server at these fixtures, add to your `.env`:

```
METEOFRANCE_API_LOCAL_MIRROR_URL=file:///absolute/path/to/snowdesk-data-pipeline/docs/research/meteofrance/bulletins-2026-05-18
```

The `file://` path must be absolute. The fetcher reads
`massif-{NN:03d}.xml` (zero-padded to three digits) from the directory.
A missing file is treated as a 404 (no bulletin today) — same as the
live path.

---

## Implementation notes

- **`bulletins/services/meteofrance_fetcher.py`** — HTTP/local-mirror
  fetch, pipeline orchestration, stash writer, `latest_meteofrance_date`.
- **`bulletins/services/meteofrance_translator.py`** — pure DPBRA XML →
  CAAML JSON translator; no I/O. Raises `MeteoFranceDelegatedRegionError`
  or `MeteoFranceTranslationError` on bad input.
- **`bulletins/services/meteofrance_massifs.py`** — static massif-ID
  catalogue (mirrors `METEOFRANCE_MASSIF_IDS`).
- **Regression tests** — `tests/bulletins/services/test_meteofrance_fetcher.py`
  covers HTTP paths, local-mirror, delegated-region skip, idempotency,
  `on_fetched` callback, stash writer, and `latest_meteofrance_date`.

For the full field-by-field translation spec, see
[`docs/meteofrance-mapping.md`](meteofrance-mapping.md).
