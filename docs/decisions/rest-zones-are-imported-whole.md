---
name: rest-zones-are-imported-whole
description: Why import_rest_zones writes SourceRestZone all or nothing (RestZoneImportError) and keys a zone by its geo.admin feature id
status: current
last-reviewed: 2026-11-02
---

# Wildlife rest zones are imported whole

## Decision

`import_rest_zones` fetches the rest-zone layer canton by canton and writes
nothing unless every canton answered and every feature has an id and a
polygon. A zone's identity is a uuid5 of its geo.admin feature id, not of
its name or its canton.

## Why

- **A sync tombstones what it does not see.** `sync_source_documents` makes
  the table equal the fetch, so a canton whose request failed would look
  like a canton whose zones were all repealed, and every instance would
  delete them on its next pull. A map that silently loses the zones in one
  canton is worse than a map one week stale. So a failure stops the run
  before the sync (`RestZoneImportError`, a non-zero exit) and last week's
  zones stand.
- **The feature id is the only stable key.** Names repeat (several
  "Wildruhezone" entries per canton) and a zone can be renamed; the feature
  id is unique across the whole layer (all 1,037 on 2 Nov 2026) and a repeat
  of it in two cantons' answers must be the same zone, which
  `collect_documents` checks.

## Consequences

- One unreachable canton holds back every canton's update for that week.
- A zone whose geometry geo.admin re-issues under a new feature id is a
  delete and an insert, not an edit. Nothing user-owned points at a zone,
  so that is harmless.
- The map layer and the draw-route warning read `RestZone` and never call
  geo.admin.
