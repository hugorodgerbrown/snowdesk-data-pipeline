---
name: self-hosted-tiles
description: Self-hosted basemap origin tiles.snowdesk.info — server in the snowdesk-tiles repo; this doc is the OPENFREEMAP_STYLE_URL + CSP cutover side
status: current
last-reviewed: 2026-07-21
---

# Runbook — self-hosted basemap origin (tiles.snowdesk.info)

## Where the origin lives

The tile server — Docker image, Caddy config, the style-rewrite script, and the
full standing-up / refresh procedure — lives in its own repo:
**[snowdesk-tiles](https://github.com/hugorodgerbrown/snowdesk-tiles)**
(SNOW-485). It has no Django code and deploys as a standalone Render web
service, which is why it is split out of this repo. Go there for: building the
planetiler extract, provisioning the Render persistent disk, DNS, the CORS /
Range config, and refreshing the monthly snapshot.

## What this repo owns — the production cutover (SNOW-242)

Pointing Snowdesk at the self-hosted origin is three changes here:

1. **Env vars** on the production service:

   ```
   OPENFREEMAP_STYLE_URL=https://tiles.snowdesk.info/styles/liberty
   BASEMAP=openfreemap_liberty
   ```

2. **CSP** — add `https://tiles.snowdesk.info` to `connect-src` in
   `config/settings/base.py` (currently only `https://tiles.openfreemap.org`
   is allowed).

3. **Frontend** — the `pmtiles://` style source requires `static/js/map.js` to
   register the PMTiles protocol (`pmtiles.js` → `maplibregl.addProtocol`).

## Cutover check

Once the `snowdesk-tiles` origin is verified live (see that repo's README, step
7) and the three changes above are deployed, load `/` and confirm the basemap
renders with **no 403 / CORS / range errors** in the network panel.
