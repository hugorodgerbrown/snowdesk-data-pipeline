---
name: environment-network-allowlist
description: Canonical egress allowlist (bare + *. pairs) for Claude Code on the web — EGRESS_BLOCKED hosts, provider APIs, basemaps, scan sites
status: current
last-reviewed: 2026-09-29
---

# Environment network allow-list

Three different blocks are recorded here, and they are **not the same
mechanism** — read the section that matches your symptom rather than
assuming one fix serves all three:

- **Claude Code on the web** — `WebFetch` returns `EGRESS_BLOCKED`, or a
  shell fetch gets `CONNECT tunnel failed, response 403`. Fixed by the
  environment's network policy on claude.ai/code. This is the original
  subject of this doc, below.
- **Claude Code's own permission classifier** — a `Bash` call is refused
  with a reason in brackets (`Exfil Scouting`, `Auto-Mode Bypass`) before
  it ever reaches the network. Fixed by a `Bash` permission rule in the
  user's settings, not by any allowlist. Added 2026-09-20 (SNOW-900).
- **The desktop app's Browser pane** — a page loaded in the pane gets HTTP
  403 on requests to third-party hosts. A different path with a different
  (and, as of 2026-09-05, unidentified) control. See the section at the
  foot.

## Claude Code on the web

Outbound network access from a Claude Code on the web session is governed
by the **environment's network policy** — configured when the environment
is created or edited, not something a session can change for itself (see
[docs](https://code.claude.com/docs/en/claude-code-on-the-web)). A session
whose policy is restrictive gets `EGRESS_BLOCKED` from every `WebFetch`
call against a domain outside that policy's allowlist, even though
`WebSearch` (which doesn't go through the same egress path) keeps working —
which is why a routine can return search-corroborated findings while
every attempt at primary-source verification fails silently underneath.

This doc exists so those failures don't just vanish into scan output: any
Snowdesk routine that hits `EGRESS_BLOCKED` records the domain here rather
than re-discovering (and re-reporting) the same block on every future run.
It is a request list for a human to action, not something a session can
apply to itself — add the domains below to the relevant environment(s) via
their network-policy settings, then move the row to "Actioned" (or delete
it) once done.

**Diagnosing a block:** `curl -sS "$HTTPS_PROXY/__agentproxy/status"`
reports proxy state (the port varies per session, so read it from the
variable rather than hard-coding one). A policy denial shows as
`curl: (56) CONNECT tunnel failed, response 403` from the shell and
`"error_type":"EGRESS_BLOCKED"` from `WebFetch` — the two agree host for
host. The target site's own bot protection comes through differently: the
tunnel is established and the *site* answers `HTTP/2 403` (e.g. a
Cloudflare `cf-mitigated: challenge`). Only the former is fixed by an
allowlist change. See `/root/.ccr/README.md` in-session for the full
diagnostic playbook.

## The complete allowlist — canonical as of 2026-09-29

**This is the list to paste into the environment's network policy.** It
supersedes the dated "Requested" sections below, which stay as the record
of *why* each domain was asked for. When a session hits a new block, add
the domain here first and a dated note below second.

**The matching rule, measured 2026-09-29: the policy matches exact hosts.**
`opensnow.com` connected while `www.opensnow.com` was refused;
`get.whympr.com` connected while `whympr.com` and `www.whympr.com` were
refused; `onxmaps.com` connected and then 301-redirected to a refused
`www.onxmaps.com`. So every domain below is listed as a **pair — the bare
domain and a `*.` wildcard** — because a wildcard does not cover its own
apex and the apex does not cover its subdomains. Two assumptions:

- **A `*.` wildcard is taken to match one label.** Where the host we need
  sits two labels down (`eu.i.posthog.com`), the wildcard is written at
  its parent (`*.i.posthog.com`) so it works under either reading. If the
  policy's wildcard turns out to match any depth, those collapse into
  their registrable domain.
- **Very large platforms get exact hosts, not a wildcard.** `*.google.com`
  and `*.apple.com` would open far more than the one store listing host a
  scan needs, so those two are listed as the single host.

The status columns below record each host **before** the paste-ready
list was applied: a direct `curl` through the session proxy on
2026-09-29, where "blocked" means the policy refused the CONNECT.

**Applied 2026-09-29, then re-probed the same day: 78 of 82 concrete
hosts connect.** That includes every host the app, the tooling and the
competitor scans actually call. The four that still fail are
`snowdesk-data.info`, `i.posthog.com`, `geopf.fr` and `www.peakvisor.com`,
and they now fail with a **502, not a 403**. The proxy logs this as
"policy denial or upstream failure": the policy lets the host through, and
the host itself doesn't answer on 443. That is most likely because it has
no DNS record, which couldn't be checked from inside a session. All four
are apex or `www.` siblings listed only for pair symmetry, not hosts
anything uses, so no action is needed. **Read a 502 as the host's problem
and a 403 as the policy's.**

### Our own infrastructure

| Allow | Hosts actually used | Status 2026-09-29 |
|---|---|---|
| `snowdesk.info`, `*.snowdesk.info` | Production site; route share links (`/routes/s/<token>/`) | bare open, `www.` blocked |
| `snowdesk-data.info`, `*.snowdesk-data.info` | `tiles.snowdesk-data.info` — basemap origin and the `/terrain/v1/` grid `sample_route_slope` reads | open |

### Bulletin providers and the EAWS

What `fetch_bulletins` calls. **Only SLF is reachable**, so a web session
cannot run an ALBINA or Météo-France ingest end to end.

| Allow | Hosts actually used | Status 2026-09-29 |
|---|---|---|
| `slf.ch`, `*.slf.ch` | `aws.slf.ch` (`SLF_API_URL`), `www.slf.ch` (competitor scan) | `aws.`/`www.` open, bare blocked |
| `avalanche.report`, `*.avalanche.report` | `static.avalanche.report` (ALBINA bulletins), `api.avalanche.report` | blocked |
| `meteofrance.fr`, `*.meteofrance.fr` | `public-api.meteofrance.fr` (DPBRA), `portail-api.meteofrance.fr` (token), `donneespubliques.meteofrance.fr` | blocked |
| `avalanches.org`, `*.avalanches.org` | `www.avalanches.org` — EAWS glossary and standards linked from the site and fixtures | blocked |

### Weather, location and analytics services

| Allow | Hosts actually used | Status 2026-09-29 |
|---|---|---|
| `open-meteo.com`, `*.open-meteo.com` | `api.`, `customer-api.`, `historical-forecast-api.`, `customer-historical-forecast-api.` — `fetch_weather` and `backfill_weather` | blocked |
| `what3words.com`, `*.what3words.com` | `api.what3words.com` (`WHAT3WORDS_API_URL`), the docs and terms | blocked |
| `w3w.co`, `*.w3w.co` | `WHAT3WORDS_MAP_BASE_URL`, the share-link host | blocked |
| `maxmind.com`, `*.maxmind.com` | GeoLite2 database download | blocked |
| `i.posthog.com`, `*.i.posthog.com` | `eu.i.posthog.com` (`POSTHOG_HOST`), `eu-assets.i.posthog.com` | blocked |

### Basemaps and map overlays

From `csp_defaults` in `config/settings/base.py` — regenerate this group
from that function, not from a style URL, when a basemap changes.

| Allow | Hosts actually used | Status 2026-09-29 |
|---|---|---|
| `geo.admin.ch`, `*.geo.admin.ch` | `vectortiles.` and the five shards `vectortiles0.`–`vectortiles4.` (swisstopo), `wmts.` (slope-angle overlay) | open |
| `openfreemap.org`, `*.openfreemap.org` | `tiles.openfreemap.org` — the fallback basemap | open |
| `geopf.fr`, `*.geopf.fr` | `data.geopf.fr` — IGN Plan (France) | open |
| `wien.gv.at`, `*.wien.gv.at` | `mapsneu.wien.gv.at` — basemap.at (Austria) | open |

### Terrain-source research (Mapterhorn, SNOW-693)

| Allow | Status 2026-09-29 |
|---|---|
| `mapterhorn.com`, `*.mapterhorn.com` (incl. `download.`) | blocked |
| `protomaps.com`, `*.protomaps.com` | blocked |
| `oliverwipfli.ch`, `*.oliverwipfli.ch` | blocked |
| `source.coop`, `*.source.coop` | blocked |
| `spatialists.ch`, `*.spatialists.ch` | blocked |

### Tooling

| Allow | Why | Status 2026-09-29 |
|---|---|---|
| `semgrep.dev`, `*.semgrep.dev` | `tox -e sast` rule packs | blocked |
| `linear.app`, `*.linear.app` | `uploads.linear.app` — ticket attachment bodies | `uploads.` open |

### Competitor scan (`docs/competitors.md`)

| Allow | Status 2026-09-29 |
|---|---|
| `whiterisk.ch`, `*.whiterisk.ch` | bare open, `www.` blocked |
| `snowsafe.at`, `*.snowsafe.at` | bare open, `www.` blocked |
| `whympr.com`, `*.whympr.com` | `get.` open, bare and `www.` blocked |
| `opensnow.com`, `*.opensnow.com` | bare open, `www.` blocked |
| `avalancheclarity.com`, `*.avalancheclarity.com` | bare open, `www.` blocked |
| `peakvisor.com`, `*.peakvisor.com` | bare open, `www.` blocked |
| `skida.app`, `*.skida.app` | open (both) |
| `onxmaps.com`, `*.onxmaps.com` | bare open but redirects to `www.`, which is blocked — **effectively blocked** |
| `aerostacks.com`, `*.aerostacks.com` | same redirect trap — **effectively blocked** |
| `bergundsteigen.com`, `*.bergundsteigen.com` | same redirect trap — **effectively blocked** |
| `granitealpinelab.com`, `*.granitealpinelab.com` | bare open, `www.` blocked |
| `sportstartups.org`, `*.sportstartups.org` | `www.` open, bare blocked |
| `swissinfo.ch`, `*.swissinfo.ch` | `www.` open, bare blocked |
| `destinet.de`, `*.destinet.de` | bare open, `www.` blocked |
| `tracxn.com`, `*.tracxn.com` | bare open, `www.` blocked |
| `the-ski-guru.com`, `*.the-ski-guru.com` | bare open, `www.` blocked |
| `uptodown.com`, `*.uptodown.com` | bare open, `www.` blocked |
| `apps.apple.com` (exact host) | open |
| `play.google.com` (exact host) | open |

**Dropped: `apkmirror.com` and `apkpure.com`.** Both were requested on
2026-08-30. The policy now lets the tunnel through, and the *site* then
answers `HTTP 403` from Cloudflare's bot challenge. No allowlist entry
fixes that, so neither is on the list; use `uptodown.com` or the store
listings for version history instead.

### Paste-ready

The 80 entries above, one per line, in the same group order:

```text
snowdesk.info
*.snowdesk.info
snowdesk-data.info
*.snowdesk-data.info
slf.ch
*.slf.ch
avalanche.report
*.avalanche.report
meteofrance.fr
*.meteofrance.fr
avalanches.org
*.avalanches.org
open-meteo.com
*.open-meteo.com
what3words.com
*.what3words.com
w3w.co
*.w3w.co
maxmind.com
*.maxmind.com
i.posthog.com
*.i.posthog.com
geo.admin.ch
*.geo.admin.ch
openfreemap.org
*.openfreemap.org
geopf.fr
*.geopf.fr
wien.gv.at
*.wien.gv.at
mapterhorn.com
*.mapterhorn.com
protomaps.com
*.protomaps.com
oliverwipfli.ch
*.oliverwipfli.ch
source.coop
*.source.coop
spatialists.ch
*.spatialists.ch
semgrep.dev
*.semgrep.dev
linear.app
*.linear.app
whiterisk.ch
*.whiterisk.ch
snowsafe.at
*.snowsafe.at
whympr.com
*.whympr.com
opensnow.com
*.opensnow.com
avalancheclarity.com
*.avalancheclarity.com
peakvisor.com
*.peakvisor.com
skida.app
*.skida.app
onxmaps.com
*.onxmaps.com
aerostacks.com
*.aerostacks.com
bergundsteigen.com
*.bergundsteigen.com
granitealpinelab.com
*.granitealpinelab.com
sportstartups.org
*.sportstartups.org
swissinfo.ch
*.swissinfo.ch
destinet.de
*.destinet.de
tracxn.com
*.tracxn.com
the-ski-guru.com
*.the-ski-guru.com
uptodown.com
*.uptodown.com
apps.apple.com
play.google.com
```

### The live policy, and what to change

*Applied 2026-09-29 — kept as the record of the change.* The
environment's policy as it stood on 2026-09-29 before the update (33
entries, copied from the settings page). It explains every probe result above: `*.slf.ch`
opens `aws.` and `www.` but not the bare `slf.ch`, and `onxmaps.com` opens
only the apex that then redirects to `www.`.

```text
*.geo.admin.ch  *.geopf.fr  *.linear.app  *.openfreemap.org  *.slf.ch
*.snowdesk-data.info  aerostacks.com  apkmirror.com  apkpure.com
apps.apple.com  avalancheclarity.com  bergundsteigen.com  destinet.de
get.whympr.com  mapsneu.wien.gv.at  onxmaps.com  opensnow.com
peakvisor.com  play.google.com  skida.app  snowdesk.info  snowsafe.at
the-ski-guru.com  tracxn.com  uptodown.com  whiterisk.ch
wmts.geo.admin.ch  typesafe.ai  docs.typesafe.ai  www.sportstartups.org
www.swissinfo.ch  granitealpinelab.com  www.skida.app
```

**Add (57):** every paste-ready entry not in the live list:

```text
*.snowdesk.info
snowdesk-data.info
slf.ch
avalanche.report
*.avalanche.report
meteofrance.fr
*.meteofrance.fr
avalanches.org
*.avalanches.org
open-meteo.com
*.open-meteo.com
what3words.com
*.what3words.com
w3w.co
*.w3w.co
maxmind.com
*.maxmind.com
i.posthog.com
*.i.posthog.com
geo.admin.ch
openfreemap.org
geopf.fr
wien.gv.at
*.wien.gv.at
mapterhorn.com
*.mapterhorn.com
protomaps.com
*.protomaps.com
oliverwipfli.ch
*.oliverwipfli.ch
source.coop
*.source.coop
spatialists.ch
*.spatialists.ch
semgrep.dev
*.semgrep.dev
linear.app
*.whiterisk.ch
*.snowsafe.at
whympr.com
*.whympr.com
*.opensnow.com
*.avalancheclarity.com
*.peakvisor.com
*.skida.app
*.onxmaps.com
*.aerostacks.com
*.bergundsteigen.com
*.granitealpinelab.com
sportstartups.org
*.sportstartups.org
swissinfo.ch
*.swissinfo.ch
*.destinet.de
*.tracxn.com
*.the-ski-guru.com
*.uptodown.com
```

**Remove (2):** `apkmirror.com`, `apkpure.com` — the site itself refuses
the request (see "Dropped" above), so the entries open nothing useful.

**Redundant once the adds land (6)** — harmless to keep, safe to delete:
`wmts.geo.admin.ch` (already covered today by `*.geo.admin.ch`),
`mapsneu.wien.gv.at`, `get.whympr.com`, `www.skida.app`,
`www.sportstartups.org` and `www.swissinfo.ch`.

**Not Snowdesk's (2):** `typesafe.ai` and `docs.typesafe.ai` appear in no
request in this doc. Presumably another project shares the environment;
leave them to whoever added them.

Not listed because the session proxy already bypasses them (its
`noProxy` setting): the Anthropic API hosts, `pypi.org`,
`files.pythonhosted.org` and `registry.npmjs.org`. GitHub is also absent
because git traffic goes through the session's own git proxy.

## History — the dated requests

Each section below records one block as it was found. The canonical list
above supersedes them; they stay as the record of why each domain was
asked for.

## Requested — 2026-09-22 (route rail design)

**Our own infrastructure is blocked, which is the costly one.** Every host
this product draws a map or samples terrain from is denied at CONNECT, on
port 443, by the web environment's network policy — so a web session can
run the app but cannot render its map or measure a route's ground. The
denial is the gateway's, not the origin's: `curl` reports
`CONNECT tunnel failed, response 403`, and `__agentproxy/status` logs it as
`connect_rejected` — "gateway answered 403 to CONNECT (policy denial)".

**The authoritative list is `csp_defaults` in `config/settings/base.py`** —
its `connect-src` / `img-src` entries are every origin the map page is
allowed to fetch from, so regenerate the rows below from that function
rather than from a style URL, which names the style host and not the hosts
the tiles actually come from.

| Domain | Why it matters |
|---|---|
| `tiles.snowdesk-data.info` | **Both of our own tilesets, one host.** `/terrain/v1/…` is the elevation grid `TERRAIN_TILE_BASE_URL` points at, which `apps/locations/services/terrain.py` samples — so `sample_route_slope` cannot run, and no route can be given a slope, an aspect, a crux, a no-fall passage or a fall line in a web session. `/styles/liberty` is the self-hosted basemap origin (`OPENFREEMAP_STYLE_URL`, [runbook](runbooks/self-hosted-tiles.md)), so the map page renders an empty canvas |
| `vectortiles.geo.admin.ch` | swisstopo winter/light: the style JSON, sprite, glyphs and both source TileJSONs — **the style document only** |
| `vectortiles0.geo.admin.ch` … `vectortiles4.geo.admin.ch` | The five numbered shards the swisstopo TileJSONs point the **tiles themselves** at (`SWISSTOPO_TILE_SHARDS`). Allowlisting the unsharded host alone loads the style and no map — the same trap SNOW-833 hit with the CSP, which has no wildcard for a subdomain prefix either |
| `wmts.geo.admin.ch` | The slope-angle raster overlay's WMTS tiles (`SLOPE_TILE_URL`) — a different host from the vector basemap, and the one SNOW-691 added |
| `tiles.openfreemap.org` | The default `OPENFREEMAP_STYLE_URL` before the self-hosted cutover, and the fallback every environment still carries |
| `data.geopf.fr` | IGN Plan IGN — the French national basemap (style JSON, vector tiles, sprites, glyphs) |
| `mapsneu.wien.gv.at` | basemap.at — the Austrian national basemap, same four |
| `snowdesk.info` | Our own production site. Already recorded under 2026-09-19 (SNOW-909) for route shares; re-confirmed blocked |

All twelve hosts were probed directly this session — the five shards
individually — and every one answered `CONNECT tunnel failed, response
403`.

**What it cost this time.** The route-rail prototype (a cursor linking the
line on the map to the same position on an unrolled strip) had to run on
*synthetic* slope angles: real coordinates and real stored elevations from
the committed corpus, with the angles rank-mapped onto a real tour's class
mix. Every design decision about where steep ground sits on a rail is
therefore being taken against a plausible distribution rather than a
measured one — the same failure mode SNOW-909 recorded a fortnight ago,
one layer further in.

## Requested — 2026-09-19 (Mapterhorn assessment)

All returned `EGRESS_BLOCKED` while assessing whether
[Mapterhorn](research/mapterhorn/README.md) should supply the terrain source
SNOW-693 adds outside Switzerland. The findings are search-corroborated only:
the coverage list, the published tileset size and the per-country native
resolutions could not be read from the projects' own pages.

| Domain | Why it matters |
|---|---|
| `mapterhorn.com` | Mapterhorn's attribution page (which national DEM covers which ground, under which licence) and data-access page (PMTiles layout, sizes, mirrors) — the two documents SNOW-693 needs before it can pick a source |
| `download.mapterhorn.com` | The PMTiles download server, and the host that serves `attribution.json` — which is what `mapterhorn.com/attribution` and `/data-access/` render from, so blocking it blocks both pages' contents as well as the archive sizes needed to plan an Alps extract |
| `protomaps.com` | Protomaps' Mapterhorn write-up, and the PMTiles format docs the extract path depends on |
| `oliverwipfli.ch` | Mapterhorn's maintainer's release notes — the only running record of what coverage has landed |
| `source.coop` | The Source Cooperative mirror of the tileset |
| `spatialists.ch` | Swiss geospatial coverage of the same, used here as corroboration |

## Requested — 2026-09-09 (SNOW-887)

Both returned `EGRESS_BLOCKED` while verifying which host a shared
what3words link should use. The format was confirmed from a screenshot of
the what3words app's own share sheet instead, but a session that needs to
check a what3words URL — or read their terms, which have already been
misread once (see
[`what3words-addresses-are-stored-indefinitely`](decisions/what3words-addresses-are-stored-indefinitely.md))
— cannot do it from here.

| Domain | Why it matters |
|---|---|
| `w3w.co` | what3words' short link host — the one `WHAT3WORDS_MAP_BASE_URL` now points at, and the one every shared pin and trip meeting point links to |
| `what3words.com` | The full site: their API docs, plan comparison and terms |

## Requested — 2026-08-30 (competitor-scan routine)

All of these returned `EGRESS_BLOCKED` on direct `WebFetch` during the
[2026-08-30 competitor scan](competitors.md) (and, for several, on prior
2026-08-19/2026-08-23 passes too — repeat blocks aren't re-listed per
pass, this is the deduplicated set as of the date above). Primary-source
verification for `docs/competitors.md` depends on these being reachable;
until then, every finding involving them is search-corroborated only.

| Domain | Why it matters |
|---|---|
| `slf.ch` | WhiteRisk / SLF — our own SLF provider's institute |
| `whiterisk.ch` | WhiteRisk product site |
| `snowsafe.at` | SnowSafe product site |
| `get.whympr.com` | Whympr product site and blog |
| `opensnow.com` | OpenSnow product site and Daily Snow |
| `avalancheclarity.com` | AvalancheClarity — blocked for 3 consecutive scan passes, the single biggest gap in verification coverage |
| `onxmaps.com` | onX Backcountry product site |
| `destinet.de` | Skitourenguru/ATHM-related coverage |
| `skida.app` | Skida (Alpine Adventures) — new entrant, one scan pass so far |
| `aerostacks.com` | Aerostacks — early-stage watch item |
| `apps.apple.com` | App Store listings for most products above |
| `play.google.com` | Play Store listings for most products above |
| `apkmirror.com` | Android version-history corroboration (OpenSnow, others) |
| `apkpure.com` | Android version-history corroboration (WhiteRisk, SnowSafe) |
| `uptodown.com` | Android version-history corroboration (SnowSafe, OpenSnow) |
| `bergundsteigen.com` | WhiteRisk redesign coverage |
| `tracxn.com` | Whympr funding/employee-count data |
| `the-ski-guru.com` | Diedamskopf geofenced-alert coverage |

## Requested — 2026-09-06 (competitor-scan routine)

New domains that returned `EGRESS_BLOCKED` on direct `WebFetch` during the
[2026-09-06 competitor scan](competitors.md), not already covered by the
2026-08-30 table above.

| Domain | Why it matters |
|---|---|
| `peakvisor.com` | PeakVisor — new entrant this pass (3D peaks/ski-touring map app with a new avalanche-bulletin layer); one scan pass so far |
| `www.skida.app` | Skida (Alpine Adventures) — `skida.app` is already listed above, but this pass's fetch was redirected to and blocked at the `www.` host specifically; listing it in case the policy matches by exact hostname |

**Also worth recording — a block that *cleared*.** `avalancheclarity.com`,
listed above as blocked on three consecutive passes (2026-08-19,
2026-08-23, 2026-08-30), was **reachable via direct `WebFetch` this pass**
(both the homepage and `/en/about/`). Nothing in this session changed the
egress policy, so either the environment's allowlist was updated by a
human between passes, or the block was intermittent rather than a fixed
denial — this doc can't tell which. Leaving the row above in place rather
than deleting it: if a future pass finds it blocked again, that confirms
intermittency; if it stays reachable, it's safe to move to "Actioned" at
that point.

## Requested — 2026-09-13 (competitor-scan routine)

Reconfirmed blocks and two clearances from the
[2026-09-13 competitor scan](competitors.md).

| Domain | Why it matters |
|---|---|
| `peakvisor.com` | PeakVisor — blocked again this pass (2nd of 2 scan passes so far) |
| `skida.app` / `www.skida.app` | Skida (Alpine Adventures) — blocked on all 3 scan passes to date; this pass's block was the deciding factor in promoting the profile on search-corroboration alone rather than waiting for primary verification |
| `www.slf.ch` | Still blocked. Note the exact host: the bare `whiterisk.ch` domain (see clearance below) is a *different* host and is no longer blocked, so this is not a duplicate of the `slf.ch` row in the 2026-08-30 table above |

**Clearances this pass** — for the record, not action items. `get.whympr.com`
and `snowsafe.at` were both reachable by direct `WebFetch` for the first
time, each returning real page content (not a placeholder), so both moved
from search-corroborated to partially primary-verified in
[`competitors.md`](competitors.md). `whiterisk.ch` (bare domain, not
`www.slf.ch`) also stopped returning `EGRESS_BLOCKED`, but the page is a
client-rendered SPA shell with no content in the fetched HTML, so this is a
policy clearance without a verification win — worth re-fetching once the
route's client-side render is reachable some other way (e.g. a rendered
snapshot), rather than assuming the plain fetch will ever return useful
content. As with `avalancheclarity.com`'s 2026-09-06 clearance, nothing in
this session changed the egress policy, so either a human updated the
allowlist between passes or the blocks were intermittent — this doc still
can't tell which.

## Requested — 2026-09-27 (competitor-scan routine)

New domains that returned `EGRESS_BLOCKED` on direct `WebFetch` during the
[2026-09-27 competitor scan](competitors.md), not already covered by the
tables above.

| Domain | Why it matters |
|---|---|
| `www.sportstartups.org` | "Top 16 Skiing Tech Startups 2026" listing — the fullest single source found this pass for scanning new ski-tech entrants beyond the named competitors |
| `www.swissinfo.ch` | Coverage of SLF/ETH Zürich's seismic and satellite/ML avalanche-detection research — background on WhiteRisk's parent institute's R&D pipeline, see [`competitors.md`](competitors.md#whiterisk) |

**Reconfirmed blocks.** `granitealpinelab.com` (blocked since the 2026-09-20
pass) and `skida.app` / `www.skida.app` (blocked since 2026-08-30/2026-09-06)
remain blocked this pass — a fifth consecutive pass for the Skida pair.

**Clearances this pass.** `www.slf.ch` was reachable by direct `WebFetch` for
the first time (two pages: the White Risk redesign news article and the
avalanche-bulletin overview page), clearing a block that held as of the
2026-09-13 pass — see [`competitors.md`](competitors.md#whiterisk).
`opensnow.com` was also reachable for the first time across eight scan
passes (blocked since 2026-08-19), confirming the Base/Premium pricing tiers
and PEAKS-model claim already recorded in the OpenSnow profile. As with the
earlier clearances recorded in this doc, nothing in this session changed the
egress policy, so either a human updated the allowlist between passes or the
blocks were intermittent — this doc still can't tell which.

## Requested — 2026-09-20 (competitor-scan routine)

New domain that returned `EGRESS_BLOCKED` on direct `WebFetch` during the
[2026-09-20 competitor scan](competitors.md), not already covered by the
2026-08-30/2026-09-06/2026-09-13 tables above.

| Domain | Why it matters |
|---|---|
| `granitealpinelab.com` | Independent gear-review site whose "Best Backcountry Skiing Apps of 2026" piece tests Granite (new entrant this pass, see [`competitors.md`](competitors.md)) alongside ten other backcountry apps, one of them tested in the Bernese Oberland — the fullest single source found for what else is worth profiling next |

**Clearances this pass.** `peakvisor.com` (both `/en/news.html` and the
avalanche-bulletin-layer announcement page) was reachable by direct
`WebFetch` for the first time after two passes of `EGRESS_BLOCKED`
(2026-09-06, 2026-09-13) — see [`competitors.md`](competitors.md) for what
that did (and didn't) resolve. `skida.app` and `www.skida.app` remain
blocked on a fourth consecutive pass.

## Requested — 2026-09-19 (SNOW-909 route breakdown design)

`snowdesk.info` returned `connect_rejected` — "gateway answered 403 to
CONNECT (policy denial)" — when following a route share link to read a real
track's geometry. The design work on the level-1 leg breakdown needs one
real per-point elevation series; without it every profile shape and every
maximum in the mocks is a plausible shape rather than a measurement.

**Render's MCP is not the way round it, and the reason is this
repository's own config.** `.claude/settings.json` lists
`mcp__*__query_render_postgres` in `permissions.deny`, alongside
`get_postgres` and `list_postgres_instances`. A denied tool is filtered out
of the toolset rather than refused on call, so it does not appear at all —
an exact-name lookup returns no match, which reads identically to the
server not offering it. Render's MCP server does ship the tool, and Claude
Chat has it, because Chat does not read this file.

Two earlier passes of this section got that wrong: the first said the
server has no SQL tool, the second blamed the connector. Both are recorded
here rather than quietly replaced, because the misdiagnosis is the
expensive part and the next session should not pay for it again. **A tool
that is absent from the toolset has been denied somewhere; check
`permissions.deny` before concluding anything about the server.**

Lifting the deny is a separate decision from this allowlist row, and one an
agent cannot make for itself — editing the deny list that governs its own
toolset is self-modification, and the permission classifier blocks it. It
needs a human hand. Were it lifted, it would sidestep egress entirely: the
query runs through Render's own API rather than this session's HTTPS
egress.

| Domain | Why it matters |
|---|---|
| `semgrep.dev` | Where `tox -e sast` fetches its rule packs (`p/django`, `p/python`, `p/security-audit`). Blocked, so semgrep exits 2 locally on a proxy error and a session cannot reproduce a CI SAST failure — the finding has to be read out of the GitHub job log instead |
| `snowdesk.info` | Our own production site. A route share link (`/routes/s/<token>/` then `/routes/routes.geojson`) is the one path a session has to a real track's points, and `routes_geojson` already answers an anonymous request holding a pending share token (SNOW-764) |

## Actioned — 2026-09-20 (SNOW-900, SLF v5 samples)

`uploads.linear.app` returned `CONNECT tunnel failed, response 403` — a
policy denial at the proxy — when fetching the three sample payloads SLF
sent on 2026-09-10, which are attached to SNOW-900 rather than committed.
**Allowed the same day**, on request, and the block cleared.

| Domain | Why it matters |
|---|---|
| `uploads.linear.app` | Where Linear serves attachment *bodies*, on five-minute signed URLs. The Linear MCP tools return the URLs and the metadata fine; only the bytes come from this host. Any ticket whose evidence is an attachment — a provider's sample payload, a spec fragment, a screenshot — is unreadable without it |

**A second, different control still bites, and it is not the egress policy.**
With the domain allowed, `curl` against it was refused by Claude Code's own
auto-mode permission classifier (`Exfil Scouting`, then `Auto-Mode Bypass`) —
a per-command permission decision, not a network one, and so not fixable from
this doc. Note the three mechanisms now recorded here: the environment's
network policy, the desktop Browser pane's 403s (below), and the permission
classifier. They fail differently and are fixed in different places; read the
error text before assuming which one you have.

**The way round it, for small files:** `mcp__Linear__get_attachment` with
`format=content` returns the file base64-encoded through the MCP server
rather than the shell, and is the natural tool for the job. It puts the whole
file in context, so it is fine for a schema fragment (SNOW-900's
`dangerRatingEvolution.json` is 460 bytes and came through this way) and
useless for the two multi-megabyte samples beside it, which is why
[`tests/sentinels/slf/new-format-preview/`](../tests/sentinels/slf/new-format-preview/README.md)
holds one of the three and a written note about the other two. A large
attachment needs either a `Bash(curl:*)` permission rule or a human to
download it.

## How to add these

1. Open the environment's settings on claude.ai/code (the environment this
   routine runs in — check which one via the session's own "current
   remote execution environment" info if unsure).
2. Find the network-policy / egress-allowlist setting and paste the
   [canonical list](#paste-ready) (no scheme; each domain as its bare
   form *and* its `*.` wildcard, because the policy matches exact hosts —
   consult the [docs](https://code.claude.com/docs/en/claude-code-on-the-web)
   if the format is unclear).
3. Once actioned, note it here (date + which domains) so a future scan
   doesn't re-request an already-granted domain, and delete or move the
   row once confirmed working.

## Requested — 2026-09-05 (desktop Browser pane: the basemaps)

A **different block from the one above**, recorded here because the
request is the same shape: domains a human has to allow somewhere.

Every basemap style, sprite, glyph and vector tile requested by a page
open in the desktop app's Browser pane returns **HTTP 403**. The map
canvas is therefore blank in every screenshot taken through the pane,
while the app's own chrome — panels, sheets, controls, anything served
from `localhost` — renders normally. This is easy to misread as a broken
map, and has been: it was mistaken for a downloads-overlay bug during
SNOW-832/835 before being isolated.

| Domain | Why it matters |
|---|---|
| `tiles.openfreemap.org` | OpenFreeMap — the default basemap's style, sprites, glyphs and tiles |
| `vectortiles.geo.admin.ch` | swisstopo winter/light — style JSON, sprites, glyphs, both source TileJSONs |
| `vectortiles0.geo.admin.ch` … `vectortiles4.geo.admin.ch` | the five sharded hosts the swisstopo TileJSON points its tiles at (SNOW-833 — same set already named in `csp_defaults`) |
| `data.geopf.fr` | IGN Plan (France) |
| `mapsneu.wien.gv.at` | basemap.at (Austria) — the host the "basemap.at" style is actually served from |

The last three are inferred from `config/settings/base.py`'s
`csp_defaults` rather than observed blocked, because nothing in this
session selected those basemaps — allow them alongside the first two or
the same 403 will surface the first time somebody previews them.

**What this is not.** Not the sites, and not the machine: `curl` against
both observed hosts from the same Mac, at the same time, returned 200. Not
the on-the-web egress policy above either — there was no agent proxy
listening on `127.0.0.1:45137`, no proxy variables in the environment, and
no network keys in any `settings.json`. So the control sits somewhere in
the desktop app's own pane, and this doc cannot yet say where. Anyone who
finds it: record it here, because the diagnostic above is the expensive
part and it should only be paid once.

**Consequence while it stands:** no visual verification through the
Browser pane can show a rendered map. Screenshots of map surfaces prove
the DOM and the app's own CSS, and nothing about the basemap beneath
them. Say so explicitly when handing one over.

## Actioned

- **2026-09-20 — `uploads.linear.app`** (SNOW-900). Allowed on the day it
  was requested; see the section of the same date above for what still
  blocks a large attachment even with the domain open.

- **By 2026-09-29 — the map infrastructure and most of the competitor
  scan**, found open when every requested host was re-probed that day
  (nobody recorded when the policy was changed):
  `tiles.snowdesk-data.info`, `vectortiles.geo.admin.ch` and
  `vectortiles0`–`4.geo.admin.ch`, `wmts.geo.admin.ch`,
  `tiles.openfreemap.org`, `data.geopf.fr`, `mapsneu.wien.gv.at`,
  `snowdesk.info`, `aws.slf.ch`, `www.slf.ch`, `whiterisk.ch`,
  `snowsafe.at`, `get.whympr.com`, `opensnow.com`, `avalancheclarity.com`,
  `peakvisor.com`, `skida.app`, `www.skida.app`, `destinet.de`,
  `tracxn.com`, `the-ski-guru.com`, `uptodown.com`, `apps.apple.com`,
  `play.google.com`, `granitealpinelab.com`, `www.sportstartups.org` and
  `www.swissinfo.ch`. These are exact hosts only — their `www.` and
  bare-apex siblings are mostly still refused, which is why the canonical
  list above pairs every domain with its wildcard.

- **2026-09-29 — the whole canonical list.** Pasted into the policy from
  the [paste-ready block](#paste-ready) the same day and re-probed: 78 of
  82 hosts connect. Among them are the ALBINA and Météo-France bulletin
  APIs, Open-Meteo, what3words, MaxMind, PostHog and `semgrep.dev`, so a
  web session can now run a full three-provider ingest, a weather fetch and
  `tox -e sast`. The four 502s are explained under
  [the canonical list](#the-complete-allowlist--canonical-as-of-2026-09-29).

Nothing is outstanding. A new block goes into the canonical list first.
