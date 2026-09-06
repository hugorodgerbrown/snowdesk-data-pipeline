# `tests/offline/` — weekly offline-maps assurance

Not the e2e suite. This one runs weekly, off the merge path, and downloads
real tiles. Read [`docs/offline-assurance.md`](../../docs/offline-assurance.md)
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
| `test_offline_toggle_is_watertight.py` | The foundation: Offline mode on, network available, nothing leaves |
| `test_downloaded_area_renders_offline.py` | The promise: it draws inside coverage, and honestly does not outside it |
| `test_dead_network_conditions.py` | The hang, not just the refusal — the case the bounded read paths exist for |
