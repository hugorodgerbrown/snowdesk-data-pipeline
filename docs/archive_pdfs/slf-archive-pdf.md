---
name: slf-archive-pdf
description: SLF archive PDF URL pattern (Bulletin_{date}_{HH-MM}_{lang}.pdf) and the TYPO3 JSON listing endpoint, 08-00/17-00 issues
status: current
last-reviewed: 2026-06-10
---

# SLF bulletin archive — PDF URL pattern

How to derive the URL of an archived SLF bulletin PDF from a date and a
language, plus the JSON listing endpoint that drives the public archive
page. Probed on **2026-06-09**.

Companion to [`slf-api-history.md`](../slf-api-history.md), which covers the
machine-readable CAAML feed. This doc covers the human-readable PDF
artefact instead.

## TL;DR

- The archive UI at
  <https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/archive/>
  doesn't embed PDF links in its HTML — it calls a TYPO3 JSON endpoint
  that returns up to three PDFs valid on a given date.
- Both the listing endpoint and the PDFs themselves are public and
  unauthenticated; the PDFs are directly fetchable without going through
  the listing first.
- Languages: **`de`, `fr`, `it`, `en`**. Anything else returns
  `language does not exist`.
- Issue times: **`08-00`** (morning update) and **`17-00`** (main
  afternoon issue, which is also the bulletin valid for the next day).
- Date coverage in the API: at least the **2025/26 season backwards
  through the 2023/24 season**, matching the CAAML feed's ~900-bulletin
  cap. The archive page claims coverage from 1998 but earlier dates
  return `httpCode: 404` — older PDFs, if they exist, are stored under
  a different layout we haven't located.

## URL patterns

### PDF (direct)

```
https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/{YYYY}/{MM}/Bulletin_{YYYY-MM-DD}_{HH-MM}_{lang}.pdf
```

Path components:

| Component   | Values                              | Notes                                              |
| ----------- | ----------------------------------- | -------------------------------------------------- |
| `{YYYY}`    | issue year                          | Folder year is the **issue** date, not validity.   |
| `{MM}`      | issue month, zero-padded            | Same — issue date.                                 |
| `{YYYY-MM-DD}` | issue date in ISO                | Repeated in the filename.                          |
| `{HH-MM}`   | `08-00` or `17-00`                  | Hyphenated, not colon-separated.                   |
| `{lang}`    | `de` / `fr` / `it` / `en`           | Matches the language of the rendered prose.        |

Examples (verified `HTTP/2 200`):

- <https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/2026/03/Bulletin_2026-03-15_17-00_en.pdf>
- <https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/2026/03/Bulletin_2026-03-15_08-00_de.pdf>
- <https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/2026/03/Bulletin_2026-03-14_17-00_fr.pdf>

### Listing endpoint

```
GET https://www.slf.ch/de/?type=1686662384&language={lang}&date={YYYY-MM-DD}
```

The `/de/` in the path is just the TYPO3 site root; the `language` query
parameter is what selects the PDF language. `type=1686662384` is the
TYPO3 page type ID that routes the request to the archive-PDF JSON
handler.

Response shape:

```json
{
  "files": [
    {
      "linkSrc": "/fileadmin/avalanche_bulletin/pdf/2026/03/Bulletin_2026-03-14_17-00_en.pdf?time=1780993957",
      "linkTitle": "Avalanche bulletin (14.03.2026 17:00, en)",
      "fileSize": 223
    },
    ...
  ],
  "httpCode": 200
}
```

For a typical winter date the response contains three entries: the
previous day's 17:00 (still valid in the morning), the same day's 08:00
update, and the same day's 17:00 issue. Late-autumn or early-spring
dates often return just one (17:00). Off-season returns `{httpCode: 404}`.

The `?time=...` suffix on `linkSrc` is a cache-buster (epoch seconds of
the file's mtime). The PDF itself is reachable without it. The same
`time` value is a useful "last regenerated" signal if we ever need to
detect a retroactive edit.

## Gotchas

- **AM/PM split is by issue time, not by elevation/zone.** The 08:00
  PDF is a morning *update* to the 17:00 issued the previous evening;
  it is not the morning half of a split-day bulletin. Day splits in
  the SLF data live inside the CAAML feed.
- **Folder year follows the issue date.** A bulletin issued on
  2025-12-31 at 17:00 (valid into 2026-01-01) sits under
  `/pdf/2025/12/`, not `/pdf/2026/01/`.
- **Listing endpoint is strict on date format.** Only `YYYY-MM-DD`
  works; `DD.MM.YYYY` (which the UI displays) returns
  `wrong date format`.
- **`language` rejects unknown codes outright** with the literal string
  `language does not exist`. No silent fallback to German.
- **Archive depth disagrees with the UI claim.** The archive page
  advertises bulletins "since 1 January 1998"; in practice the JSON
  endpoint returns 404 for `1998-01-01` and other early dates.
  Treat the effective floor as the same ~2.5-season window the CAAML
  feed exposes, not the marketing claim.

## Deriving a URL

The construction is dependency-free — no network call needed once you
know the date and language. Use the listing endpoint only when you
need to confirm the bulletin actually exists or to discover whether an
08:00 update was issued.

```python
"""Helpers for deriving SLF archive PDF URLs from a date and language."""

from __future__ import annotations

import datetime as dt
from typing import Literal

SlfLanguage = Literal["de", "fr", "it", "en"]
SlfIssueTime = Literal["08-00", "17-00"]

PDF_BASE = "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf"


def slf_pdf_url(
    issue_date: dt.date,
    issue_time: SlfIssueTime,
    language: SlfLanguage,
) -> str:
    """Return the public URL for an SLF bulletin PDF.

    ``issue_date`` is the date the bulletin was *issued*, which determines
    both the folder path and the filename prefix. For an 08:00 update this
    is the same calendar day; for a 17:00 issue the bulletin is valid into
    the following day but the URL still uses the issue date.
    """
    return (
        f"{PDF_BASE}/{issue_date:%Y}/{issue_date:%m}/"
        f"Bulletin_{issue_date:%Y-%m-%d}_{issue_time}_{language}.pdf"
    )


def slf_pdf_urls_for_validity(
    valid_date: dt.date,
    language: SlfLanguage,
) -> dict[str, str]:
    """Return the three PDF URLs that may carry a bulletin valid on ``valid_date``.

    The 17:00 issue from the previous day, the 08:00 update on the day
    itself, and the 17:00 issue on the day itself. Not all three exist for
    every date — confirm via the listing endpoint or a HEAD request.
    """
    previous = valid_date - dt.timedelta(days=1)
    return {
        "previous_evening": slf_pdf_url(previous, "17-00", language),
        "morning_update": slf_pdf_url(valid_date, "08-00", language),
        "evening_issue": slf_pdf_url(valid_date, "17-00", language),
    }


if __name__ == "__main__":
    url = slf_pdf_url(dt.date(2026, 3, 15), "17-00", "en")
    assert url == (
        "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/"
        "2026/03/Bulletin_2026-03-15_17-00_en.pdf"
    )
    print(url)
```

If you do need to round-trip through the listing endpoint — for example
to discover whether a given date has a morning update — the call is:

```python
import requests

def slf_archive_listing(date: dt.date, language: SlfLanguage) -> dict:
    """Return the JSON archive listing for ``date`` in ``language``.

    Returns ``{"httpCode": 404}`` for dates with no bulletins (off-season,
    pre-archive). Returns ``{"files": [...], "httpCode": 200}`` otherwise.
    """
    response = requests.get(
        "https://www.slf.ch/de/",
        params={
            "type": "1686662384",
            "language": language,
            "date": date.isoformat(),
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
```
