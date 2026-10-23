---
name: data-reaches-other-environments-through-a-feed
description: Why staging pulls production data over the HTTPS feed /api/data/v1/ (client credentials), not a database credential
status: current
last-reviewed: 2026-10-23
---

# Data reaches other environments through a feed

**Decision.** Production publishes the data it ingests (bulletins, weather,
and the curated locations and resorts) as a read-only HTTPS feed at
`/api/data/v1/` (SNOW-1197). The feed is authenticated by OAuth 2 client
credentials, issued only to service clients that staff create by hand. A
client pulls with `pull_production_data`, which is incremental and
idempotent and replays each bulletin through its own `upsert_bulletin`.
`bin/sync-staging-data` stays, but only as a wholesale reset.

**Why.**

- **Staging is the first client, not the only one.** A database copy
  needs a production database credential on every consumer, and each
  consumer would see production's schema, which no external service
  should. A feed is a contract: versioned in the URL, with allowlisted
  columns and natural keys that two independently seeded databases share.
- **Bulletins are replayed, not copied.** Replaying the CAAML through the
  client's own ingest means staging's region links, day ratings and render
  models are produced by staging's code. A copy would show production's
  output and hide any regression in the code being staged.
- **Idempotent and incremental.** The pg-level sync is a full replace that
  wipes staging's favourites, observations and trips each time, so nobody
  ran it often. A pull of what changed since the last pull is cheap and
  safe to repeat, so it can be run whenever staging looks stale.
- **Client credentials on the existing OAuth server**, rather than an
  API-key model or a shared secret. Each client gets its own revocable
  identity at no extra cost, and nothing new holds a credential.

**Rejected.**

- *A shared secret.* Every client would share it, and rotating it breaks
  them all.
- *An `ApiKey` model.* It is a second credential store next to one that
  already hashes and expires tokens.
- *Restoring the nightly `sync-staging-data` cron.* It doesn't help any
  client other than staging, and it still clears staging's own data.

**Consequences.**

- A client-credentials token has no user. The token endpoint issues one
  only to a service client (`apps.oauth.validators`), because open DCR
  would otherwise hand one to anyone. The MCP connect rule refuses a
  userless token instead of crashing on it.
- The feed never deletes anything on the client. Removals reach staging
  only through a reset.
- A ~230-statement bulk ORM upsert put staging's database into crash
  recovery once (SNOW-736). That was a full-table copy in one pass. The
  pull applies items one at a time through the normal ingest path, the
  same load as a scheduled `fetch_bulletins`. A deep `--since` backfill
  is slow rather than heavy. For a full rebuild, use the reset script.
