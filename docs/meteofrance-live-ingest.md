---
name: meteofrance-live-ingest
description: Météo-France DPBRA ingest operations — fetch_bulletins --source meteofrance, METEOFRANCE_API_KEY on Render, --local-mirror
status: current
last-reviewed: 2026-11-02
---

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

Cadence: every hour at :02 and :07 UTC (`2,7 * * * *`, off the top of the hour since SNOW-1380), driven by the
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

Full list: [`apps/ingest/normalise/meteofrance/massifs.json`](../apps/ingest/normalise/meteofrance/massifs.json).

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

Off-season (roughly late spring to mid-December) every massif answers
404, which the walk logs as "No bulletin for MeteoFrance massif N today"
and counts as skipped. That is not a fault: a run with 35 skips and no
failures is the expected shape between seasons.

### Connection refused (SNOW-1380)

`[Errno 111] Connection refused` from `public-api.meteofrance.fr` means
Météo-France's gateway rejected the TCP connection before any HTTP was
exchanged, so it is never the key (that is a 401), the APIM quota (a 429)
or an empty feed (a 404). Through October 2026 it hit about one run in
twenty, at a random massif, most often in the run at the top of the hour.

What a run does about it:

- **One connection per run.** `dpbra_session` is a keep-alive session the
  walk builds once, so a run opens one TCP+TLS connection rather than one
  per massif.
- **A bounded connect retry.** That session retries a failed connect up
  to `CONNECT_RETRIES` (3) times, immediately and then after 1 s and 2 s.
  Nothing else is retried: an HTTP status surfaces at once.
- **One massif, one failure.** If every attempt fails, that massif counts
  as `meteofrance_failed` and the walk carries on. The command still exits
  non-zero, so the scheduler and Sentry see it, but every other massif is
  fetched that hour.
- **Off the hour.** The schedule fires at :02 and :07, not :00 and :05.

A refusal that persists across a whole run (every massif failed) is an
outage on Météo-France's side or a block on Render's egress address, not
something a retry fixes. A missing `METEOFRANCE_API_KEY` on a live URL now
fails the run once, before any request, instead of once per massif.

---

## API key requirement

The DPBRA APIM requires an `apikey` header on every request:

```
GET https://public-api.meteofrance.fr/public/DPBRA/v1/massif/{id}/BRA
apikey: <METEOFRANCE_API_KEY>
```

Without a valid key, `fetch_meteofrance_bulletin` raises `RuntimeError`
immediately and the massif is counted as a failure.

That is what a Claude Code cloud session sees, by design: the key is
never given to one
([decision](decisions/cloud-sessions-hold-no-provider-credentials.md)),
so a live Météo-France fetch is production's job and a session uses
`--local-mirror` instead. Nothing in the test suite or the seeds needs the
key.

---

## Render deployment — env var wiring

`METEOFRANCE_API_KEY` lives in the `Production` env-var group in the
Render dashboard, so every production service that references
`fromGroup: Production` in [`render.yaml`](../render.yaml) inherits it —
`snowdesk-website`, `snowdesk-scheduler` and `snowdesk-background-tasks`.
There is no per-service override; a single dashboard edit reaches every
consumer of the key.

The key is deliberately dashboard-managed rather than declared in
`render.yaml`: Blueprint auto-sync does not touch env-group contents, so
rotating the key is a group edit that never needs a repo change or a
service restart trigger. If the group entry ever gets removed, every
scheduled `fetch_bulletins --source meteofrance` run raises
`RuntimeError` for every massif and logs 35 failures per run — check the
group first if that appears.

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
uv run python manage.py fetch_bulletins --source meteofrance --commit --force
```

---

## Offline testing — `--local-mirror`

The `--local-mirror` flag points the fetcher at a local file-system
directory instead of the live APIM, so no API key is needed and no
network I/O occurs:

```bash
# In development the mirror defaults to the committed sample below, so no
# .env line is needed. Override it there to point elsewhere:
#   METEOFRANCE_API_LOCAL_MIRROR_URL=file:///path/to/another/mirror

# Run with local mirror (read-only):
uv run python manage.py fetch_bulletins --source meteofrance --local-mirror

# Run with local mirror and persist:
uv run python manage.py fetch_bulletins --source meteofrance --local-mirror --commit
```

The bundled research fixtures in
`docs/research/meteofrance/bulletins-2026-05-18/` hold 36 `massif-*.xml`
files, including the delegated-region file (`massif-071.xml`); a fetch
reads 35 of them. They are the reference inputs for the fetcher
regression tests, and since SNOW-1241 they are the development default
for `METEOFRANCE_API_LOCAL_MIRROR_URL` (`config/settings/development.py`).
Staging and production have no default.

An override must be an absolute `file://` path. The fetcher reads
`massif-{NN:03d}.xml` (zero-padded to three digits) from the directory.
A missing file is treated as a 404 (no bulletin today) — same as the
live path.

---

## Implementation notes

- **`apps/ingest/sources/meteofrance.py`** — the HTTP/local-mirror fetch
  (`fetch_meteofrance_bulletin`), the per-run `dpbra_session` and its
  connect retry; the walk across massifs is `iter_meteofrance` in
  `apps/ingest/runs.py`.
- **`apps/bulletins/services/meteofrance_fetcher.py`** — pipeline
  orchestration, stash writer, `latest_meteofrance_date`.
- **`apps/ingest/normalise/meteofrance/translator.py`** — pure DPBRA XML →
  CAAML JSON translator; no I/O. Raises `MeteoFranceDelegatedRegionError`
  or `MeteoFranceTranslationError` on bad input.
- **`apps/ingest/normalise/meteofrance/massifs.py`** — the canonical
  massif *name* list (`ALPINE_MASSIFS`, `ALL_MASSIFS`) and the slug →
  integer code lookup (`SLUG_TO_CODE`, `slug_to_region_id`), built from
  `docs/research/meteofrance/massifs.json`. Used by the archive loader and
  the offline scripts; the live fetcher's loop reads
  `settings.METEOFRANCE_MASSIF_IDS` directly.
- **Regression tests** — `tests/bulletins/services/test_meteofrance_fetcher.py`
  covers HTTP paths, local-mirror, delegated-region skip, idempotency,
  `on_fetched` callback, stash writer, and `latest_meteofrance_date`.

For the full field-by-field translation spec, see
[`docs/meteofrance-mapping.md`](meteofrance-mapping.md).
