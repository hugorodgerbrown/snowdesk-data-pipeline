---
name: geojson-feature-envelope
description: Raw bulletins are stored wrapped in a GeoJSON Feature envelope regardless of provider
status: current
last-reviewed: 2026-06-10
---

# Raw bulletins stored in a GeoJSON Feature envelope

**Decision.** Every raw bulletin — from any provider — is wrapped before
storage so `Bulletin.raw_data` always holds
`{ "type": "Feature", "geometry": null, "properties": { …raw CAAML… } }`.
The wrap happens in `upsert_bulletin()` (`bulletins/services/slf_fetcher.py`),
which all provider fetchers share.

**Why.** Downstream consumers (render model builder, admin raw viewer,
exports) see one stable envelope shape regardless of provider, and the
envelope leaves room to attach geometry later without a migration of the
stored payloads.

**Consequences.** Code reading `raw_data` always unwraps via
`["properties"]`. New providers must route storage through
`upsert_bulletin()` rather than writing `raw_data` directly.
