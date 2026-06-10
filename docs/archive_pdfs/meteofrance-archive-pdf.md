---
name: meteofrance-archive-pdf
description: Météo-France BRA archive PDF URLs — bra.YYYYMMDD.json daily index and BRA.{MASSIF}.{HEURES}.pdf on donneespubliques
status: current
last-reviewed: 2026-06-10
---

# Météo-France BRA archive — PDF URL scheme

How to derive the public PDF URL for a historical Météo-France BRA
(Bulletin de Risque d'Avalanche) given a date and a massif.

> **Decommissioning warning.** The
> [`donneespubliques.meteofrance.fr`](https://donneespubliques.meteofrance.fr/)
> portal is being retired in favour of a new Confluence portal. Once that
> migration is complete these URLs stop serving. The committed fixtures under
> [`tests/scripts/meteofrance_archive/fixtures/`](../tests/scripts/meteofrance_archive/fixtures/)
> are the only long-term guarantee. See
> [`scripts/meteofrance-archive/README.md`](../scripts/meteofrance-archive/README.md)
> for the wider backfill pipeline that depends on these URLs.

---

## URL shape

There are two endpoints. You always need both — the per-bulletin URL
embeds a publication timestamp that is **not** derivable from the date
alone, so you have to ask the daily index for it first.

### Daily index (JSON)

```
https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA/bra.YYYYMMDD.json
```

Returns a JSON array, one entry per massif that published on that date:

```json
[
  {"massif": "CHABLAIS",   "heures": ["20260115100000"]},
  {"massif": "MONT-BLANC", "heures": ["20260115100000", "20260115153000"]}
]
```

- `massif` — upper-case slug (e.g. `CHABLAIS`, `MONT-BLANC`, `HAUTE-TARENTAISE`).
- `heures` — list of publication timestamps for that (massif, date), each a
  14-digit `YYYYMMDDHHMMSS` string in local time. Multiple entries are
  re-publishes; the last entry wins.
- A `404` on this endpoint means **no bulletins were issued that day**
  (off-season, weekend gap, etc.) — treat as empty, not as an error.

### Per-bulletin PDF

```
https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA/BRA.{MASSIF}.{HEURES}.pdf
```

- `{MASSIF}` — exact massif slug from the index response.
- `{HEURES}` — one of the timestamps from the index `heures` list.

Example:

```
https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA/BRA.CHABLAIS.20260115100000.pdf
```

---

## Why the timestamp can't be guessed

Publication time varies by massif and by day. Most bulletins land near
`10:00:00` local time, but re-publishes (correction, updated forecast)
happen at arbitrary times the same day. There is no schedule you can
hard-code — the daily index is the source of truth for which timestamps
exist.

---

## Code sample

A self-contained function that resolves the PDF URL for a given
`(date, massif)` and downloads the bytes. Mirrors the conventions used
by [`mf_bra_index_archive.py`](../scripts/meteofrance-archive/mf_bra_index_archive.py)
and [`mf_bra_url_discover.py`](../scripts/meteofrance-archive/mf_bra_url_discover.py):
shared `User-Agent`, 30 s timeout, polite 0.2 s delay between requests.

```python
"""Resolve and download a Météo-France BRA archive PDF."""

from __future__ import annotations

import time
from datetime import date

import requests

BASE_URL = "https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA"
USER_AGENT = "snowdesk-backfill/1.0 (contact@snowdesk.app)"
RATE_LIMIT_S = 0.2


def fetch_index(session: requests.Session, bulletin_date: date) -> list[dict]:
    """Return the daily index for ``bulletin_date``.

    Each entry is ``{"massif": <slug>, "heures": [<YYYYMMDDHHMMSS>, ...]}``.
    Returns an empty list when no bulletins were issued that day (HTTP 404).
    """
    url = f"{BASE_URL}/bra.{bulletin_date:%Y%m%d}.json"
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json()


def resolve_pdf_url(
    session: requests.Session,
    bulletin_date: date,
    massif: str,
) -> str | None:
    """Return the PDF URL for ``(bulletin_date, massif)``, or None if absent.

    When a massif republishes the same day, picks the latest ``heures`` value —
    that matches the behaviour of the existing backfill pipeline.
    """
    index = fetch_index(session, bulletin_date)
    for entry in index:
        if entry["massif"] != massif:
            continue
        heures = entry.get("heures") or []
        if not heures:
            return None
        latest = sorted(heures)[-1]
        return f"{BASE_URL}/BRA.{massif}.{latest}.pdf"
    return None


def download_pdf(bulletin_date: date, massif: str, dest_path: str) -> bool:
    """Download the BRA PDF for ``(bulletin_date, massif)`` to ``dest_path``.

    Returns True on success, False if the bulletin doesn't exist for that day.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    url = resolve_pdf_url(session, bulletin_date, massif)
    if url is None:
        return False

    time.sleep(RATE_LIMIT_S)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    with open(dest_path, "wb") as fh:
        fh.write(resp.content)
    return True


if __name__ == "__main__":
    ok = download_pdf(date(2026, 1, 15), "CHABLAIS", "BRA.CHABLAIS.2026-01-15.pdf")
    print("downloaded" if ok else "no bulletin that day")
```

---

## Notes for batch use

If you need to pull a whole season, **don't** loop this snippet — use the
existing four-stage pipeline in
[`scripts/meteofrance-archive/`](../scripts/meteofrance-archive/). It is
resumable, splits index fetching from PDF download, and hands the heavy
work to `aria2c` for parallel transfer. See its
[`README.md`](../scripts/meteofrance-archive/README.md) for the full
sequence.

The snippet above is for **ad-hoc lookups** — debugging a single
bulletin, checking whether a date was published, sanity-checking a
massif slug against the live index.
