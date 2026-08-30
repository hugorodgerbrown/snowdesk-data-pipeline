---
name: environment-network-allowlist
description: Domains requesting network-egress allowlisting for Claude Code on the web environments — accumulated from routines hitting EGRESS_BLOCKED
status: current
last-reviewed: 2026-08-30
---

# Environment network allow-list

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

## Actioned

*(none yet — first request, 2026-08-30)*
