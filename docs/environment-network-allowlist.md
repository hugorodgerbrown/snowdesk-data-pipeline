---
name: environment-network-allowlist
description: Domains needing egress allowlisting for Claude Code — web routines hitting EGRESS_BLOCKED, and the Browser pane 403ing every basemap tile
status: current
last-reviewed: 2026-09-06
---

# Environment network allow-list

Two different blocks are recorded here, and they are **not the same
mechanism** — read the section that matches your symptom rather than
assuming one fix serves both:

- **Claude Code on the web** — `WebFetch` returns `EGRESS_BLOCKED`. Fixed
  by the environment's network policy on claude.ai/code. This is the
  original subject of this doc, below.
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

*(none yet — 2026-08-30 and 2026-09-05 requests both outstanding)*
