---
name: albina-archive-pdf
description: ALBINA archive PDF URLs — api.avalanche.report bulletins/pdf endpoint, validTime.startTime lookup, legacy static paths
status: current
last-reviewed: 2026-06-10
---

# ALBINA bulletin archive PDF URL pattern

Reference for constructing the download URL of an archived ALBINA
bulletin PDF. Verified against the live API on 2026-06-09 by tracing
the URL builders in the upstream
[`albina-website`](https://gitlab.com/albina-euregio/albina-website)
React app and probing the resolved endpoints.

## TL;DR

ALBINA bulletins exist in two eras and the URL pattern differs:

1. **Modern bulletins (ALBINA, ~2018-12 onwards).** The PDF is rendered
   on demand by the ALBINA API. The `PDF` button on
   `https://lawinen-warnung.eu/bulletin/{YYYY-MM-DD}` resolves to:

   ```
   https://api.avalanche.report/albina/api/bulletins/pdf
     ?date={validTime.startTime}
     &region={region}
     &lang={lang}
     &grayscale={true|false}
     [&microRegionId={microRegionId}]
   ```

2. **Legacy regional bulletins (pre-ALBINA).** Each member service
   uploaded its own static PDF to `static.avalanche.report` and the
   archive page surfaces those directly:

   ```
   https://static.avalanche.report/bulletins/archive/tyrol/pdf/{YYYY-MM-DD}_0730_lwdtirol_lagebericht.pdf
   https://static.avalanche.report/bulletins/archive/south_tyrol/pdf/{YYYY-MM-DD}.{de|it}.pdf
   https://static.avalanche.report/bulletins/archive/trentino/pdf/{YYYY-MM-DD}_valanghe_it.pdf
   ```

## Modern PDF endpoint — parameter reference

| Param | Required | Value |
|---|---|---|
| `date` | yes | The bulletin's `validTime.startTime` as ISO-8601 UTC, **not** the calendar date the bulletin is valid *for*. ALBINA publishes the evening before, so a bulletin valid for `2025-12-01` has `startTime` of `2025-11-30T16:00:00Z` (17:00 local CET). Both `Z` and `.000Z` suffixes parse. |
| `region` | yes | `EUREGIO` for the multi-region report, or a province code: `AT-07` (Tyrol), `IT-32-BZ` (South Tyrol), `IT-32-TN` (Trentino). |
| `lang` | yes | Two-letter language code: `en`, `de`, `it`, `fr`, `es`, `ca`, `oc`. Not every language is rendered for every region. |
| `grayscale` | yes | `false` for the colour PDF, `true` for grayscale. Omitting this returns `400 Bad Request`. |
| `microRegionId` | no | Optional micro-region ID like `AT-07-19`. Narrows the PDF to a single micro-region. |

The `date` value should come from the CAAMLv6 JSON, not be constructed.
Every bulletin file exposes its own `validTime.startTime` and that is
what the website passes through.

### Deriving `validTime.startTime`

For a target calendar date `YYYY-MM-DD` and region code `REGION` (one of
`AT-07`, `IT-32-BZ`, `IT-32-TN`), the static CAAMLv6 JSON lives at:

```
https://static.avalanche.report/bulletins/{YYYY-MM-DD}/{YYYY-MM-DD}_{REGION}_{lang}_CAAMLv6.json
```

The first bulletin in the file has the `validTime.startTime` value
needed by the PDF endpoint. There is no EUREGIO-wide static file — pick
any province file (they share the same `validTime`).

### Worked example

For the bulletin valid for `2025-12-01`:

```
GET https://static.avalanche.report/bulletins/2025-12-01/2025-12-01_AT-07_en_CAAMLv6.json
→ bulletins[0].validTime.startTime = "2025-11-30T16:00:00Z"

GET https://api.avalanche.report/albina/api/bulletins/pdf
      ?date=2025-11-30T16:00:00Z
      &region=EUREGIO
      &lang=en
      &grayscale=false
→ 200, application/pdf, ~1.8 MB, 8 pages
```

## Code sample

A minimal Python helper using only the standard library plus `requests`.
The two-step shape is deliberate: derive `validTime.startTime` from the
static JSON, then construct the API URL. Avoid hard-coding `T16:00:00Z`
— DST and editorial changes have shifted the publication offset
historically, and the JSON is authoritative.

```python
"""Derive the ALBINA archive PDF URL for a given bulletin date."""

from urllib.parse import urlencode

import requests

ALBINA_STATIC = "https://static.avalanche.report"
ALBINA_API = "https://api.avalanche.report"


def albina_pdf_url(
    date: str,
    region: str = "EUREGIO",
    lang: str = "en",
    *,
    grayscale: bool = False,
    micro_region_id: str | None = None,
) -> str:
    """Return the dynamic ALBINA PDF URL for a bulletin.

    ``date`` is the calendar date the bulletin is *valid for*
    (``YYYY-MM-DD``); the function resolves the bulletin's
    ``validTime.startTime`` from the static CAAMLv6 JSON and passes
    that through to the PDF endpoint. ``region`` is ``EUREGIO`` or one
    of the province codes (``AT-07``, ``IT-32-BZ``, ``IT-32-TN``).
    """
    # EUREGIO has no static file; any province file shares the same
    # validTime, so AT-07 is a safe fallback for the lookup.
    lookup_region = "AT-07" if region == "EUREGIO" else region
    json_url = (
        f"{ALBINA_STATIC}/bulletins/{date}/"
        f"{date}_{lookup_region}_{lang}_CAAMLv6.json"
    )
    payload = requests.get(json_url, timeout=30)
    payload.raise_for_status()
    start_time = payload.json()["bulletins"][0]["validTime"]["startTime"]

    params = {
        "date": start_time,
        "region": region,
        "lang": lang,
        "grayscale": "true" if grayscale else "false",
    }
    if micro_region_id:
        params["microRegionId"] = micro_region_id
    return f"{ALBINA_API}/albina/api/bulletins/pdf?{urlencode(params)}"


if __name__ == "__main__":
    print(albina_pdf_url("2025-12-01", region="EUREGIO", lang="en"))
    # → https://api.avalanche.report/albina/api/bulletins/pdf
    #     ?date=2025-11-30T16%3A00%3A00Z&region=EUREGIO&lang=en&grayscale=false
```

The endpoint returns `application/pdf` directly — stream it to disk or
hand it off as a redirect target. Generation takes a few seconds for
the multi-language EUREGIO report; cache the resulting bytes if you
expect repeat downloads of the same `(date, region, lang)` triple.

## Sources

- URL templates: [albina-website `app/config.json`](https://gitlab.com/albina-euregio/albina-website/-/blob/master/app/config.json) — keys under `apis.bulletin.*`.
- PDF button call site: [`app/components/bulletin/bulletin-report.tsx:238`](https://gitlab.com/albina-euregio/albina-website/-/blob/master/app/components/bulletin/bulletin-report.tsx#L238).
- Legacy archive URLs: [`app/views/archive.tsx:252`](https://gitlab.com/albina-euregio/albina-website/-/blob/master/app/views/archive.tsx#L252).
- Probed live on 2026-06-09 against the `lawinen-warnung.eu` /
  `avalanche.report` deployment; all region × language combinations
  listed above returned a valid PDF.
