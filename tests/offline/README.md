# `tests/offline/` — offline-maps assurance

Not the e2e suite. This one downloads real tiles, runs on every PR that
touches the download path, and weekly. Read [`docs/offline-assurance.md`](../../docs/offline-assurance.md)
before adding to it — the design decisions, the proxy's three modes, the
fuzzing contract and the two measurement caveats are all there.

```bash
uv run tox -e offline                                  # this ISO week's draw
SNOWDESK_OFFLINE_SEED=2026-W37 uv run tox -e offline   # reproduce
```

| File | What it holds |
|---|---|
| `proxy.py` | The recording, switchable HTTP/HTTPS proxy — the only thing in the repo that can see a service worker's own traffic |
| `fuzz.py` | The seeded draw: region, basemap, zooms, bearing |
| `conftest.py` | `offline_map_page` — signed in, SW-controlled, behind the proxy — and the helpers that drive the product's own controls |
| `test_offline_toggle_is_watertight.py` | The foundation: Offline mode on, network available, nothing leaves — on both of `sw.js`'s cross-origin routes (SNOW-854) |
| `test_downloaded_area_renders_offline.py` | The promise: it draws inside coverage, and honestly does not outside it |
| `test_update_keeps_downloaded_content.py` | SNOW-1311: a service-worker update carries the downloaded bulletins and weather sheets into the new shell cache |
| `test_martigny_verbier_downloads.py` | Fixed subject, every map flag on: a region from a fresh worker and after an update, a custom area, Sync all ([why](../../docs/decisions/a-real-download-gates-download-prs.md)) |
| `test_dead_network_conditions.py` | The hang, not just the refusal — the case the bounded read paths exist for |
