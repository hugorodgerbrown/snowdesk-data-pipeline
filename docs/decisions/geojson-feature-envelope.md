---
name: geojson-feature-envelope
description: Raw CAAML surfaces (bulletin_caaml, get_bulletin_raw, admin, superuser embed) wrap SourceBulletin.caaml via caaml_envelope
status: current
last-reviewed: 2026-11-01
---

# Raw bulletins are served in a GeoJSON Feature envelope

**Decision.** Every raw bulletin — from any provider — is served wrapped as
`{ "type": "Feature", "geometry": null, "properties": { …raw CAAML… } }`.
Until SNOW-1337 the wrap happened at storage, in `upsert_bulletin()`, and
`Bulletin.raw_data` held the envelope. Since then a `Bulletin` keeps no
CAAML: ingest's `SourceBulletin.caaml` holds the bare properties, and the
raw surfaces (`bulletin_caaml`, MCP `get_bulletin_raw`, the superuser /
`DEBUG` embed, the admin's CAAML block) wrap them on the way out with
`apps.ingest.public.caaml_envelope`.

**Why.** Downstream consumers (exports, the MCP tool's callers, anyone
reading the page source) see one stable envelope shape regardless of
provider, and the envelope leaves room to attach geometry later without
changing what a consumer unwraps.

**Consequences.** A consumer of a raw surface unwraps via `["properties"]`.
A new raw surface wraps with `caaml_envelope`, never by hand.
