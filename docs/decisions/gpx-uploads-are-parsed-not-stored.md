---
name: gpx-uploads-are-parsed-not-stored
description: Uploaded .gpx files are parsed into Route.points and discarded — no FileField, no MEDIA_ROOT, no object storage
status: current
last-reviewed: 2026-08-17
---

# GPX uploads are parsed, not stored (SNOW-685)

**Decision.** An uploaded `.gpx` is read into memory, parsed by
`apps.routes.services.gpx.parse_gpx`, and dropped. What persists is the
derived `Route` row — the coordinate list in `points`, the derived
`distance_m` / `ascent_m` / `point_count` / `bounds`, and the original
filename as a label in `source_filename`. There is no `FileField` on
`Route` and no copy of the original anywhere.

**Why.** Keeping the original file is not a small addition here — it is an
infrastructure decision wearing a model field's clothes.

This project has no `MEDIA_ROOT`, no `DEFAULT_FILE_STORAGE` override and no
`FileField` on any model; every byte it serves is either a static asset or
a database row. Render web dynos have ephemeral disk, so a `FileField`
backed by local storage would lose its files on the next deploy — the
failure would be silent, and it would not show up in development, where
the disk persists. Storing the originals properly therefore means adding
an object-storage backend, credentials, a lifecycle policy and a deletion
path that stays in step with `Route.delete()`. That is worth doing when
something needs it; nothing does.

Nothing of consequence is lost. GPX is XML over a coordinate list, so a
re-export from `points` reconstructs a usable file whenever one is wanted —
the only casualties are the extension elements this parser already ignores
(heart rate, cadence, per-point timestamps), none of which any planned
surface reads. The route the user sees drawn is `points`, not the upload,
so the stored row is the authoritative artefact either way.

The privacy arithmetic points the same way. A GPX track is location
history; the less of it retained, and the fewer places it is retained in,
the smaller the exposure. Simplification (see `MAX_POINTS` in
`services/gpx.py`) already discards most of the raw fidelity by design.

## Consequences

- `Route` has no `FileField`, and adding one is a decision to be re-opened
  here rather than a detail of a later ticket.
- An upload that parses is stored; an upload that does not is a 400 with
  nothing kept. There is no "quarantine the bad file and look at it later"
  path, and diagnosing a rejected file means asking the user for it.
- Re-processing a track under improved parsing rules is not possible for
  routes already imported — a rules change applies to new uploads only.
  If that ever needs to change, retaining originals is the prerequisite,
  and that is the moment to add object storage.
- Per-point timestamps, heart rate and other GPX extensions are gone at
  ingest. A feature that needs them needs this decision revisited first.
