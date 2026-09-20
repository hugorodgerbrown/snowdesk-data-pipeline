---
name: environment-network-allowlist
description: Domains needing egress allowlisting for Claude Code — web routines hitting EGRESS_BLOCKED, and the Browser pane 403ing every basemap tile
status: current
last-reviewed: 2026-09-20
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

**Diagnosing a block:** `curl -sS
http://127.0.0.1:45137/__agentproxy/status` reports proxy state; a
`WebFetch` failure with `"error_type":"EGRESS_BLOCKED"` is the policy, not
the target site's own bot protection (a 403/timeout *from the site itself*
would come through differently — see `/root/.ccr/README.md` in-session for
the full diagnostic playbook). Only the former is fixed by an allowlist
change.

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
2. Find the network-policy / egress-allowlist setting and add the domains
   above (bare domain, no scheme — match however the policy UI expects
   entries; consult the [docs](https://code.claude.com/docs/en/claude-code-on-the-web)
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

The 2026-08-30, 2026-09-05, 2026-09-06, 2026-09-09, 2026-09-13, 2026-09-19
and 2026-09-20 competitor-scan requests are all still outstanding.
