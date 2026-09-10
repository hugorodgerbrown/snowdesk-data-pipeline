---
name: boundaries-follow-the-basemap
description: EAWS boundary outlines follow the active basemap, not the Bulletins rows (BASEMAP_COUNTRIES, data-basemap-countries, boundaryCountryCodes)
status: current
last-reviewed: 2026-09-10
---

# The boundary outlines follow the basemap, not the Bulletins rows

**Decision.** The layers menu's **Bulletins** rows (SLF, Météo-France,
ALBINA) filter bulletin **data** only — `regions-fill`, the danger
choropleth, and `bulletin-groupings-line`, the per-provider boundary. The
six **Boundaries** layers — `regions-line`, `regions-label`,
`sub-regions-line`, `sub-regions-label`, `major-regions-line`,
`major-regions-label` — are scoped by the **active basemap's** country
coverage instead.

That coverage is declared once, server-side, in
`settings.BASEMAP_COUNTRIES` (`config/settings/base.py`): OpenFreeMap draws
all four countries, `swisstopo_winter` and `swisstopo_light` draw CH,
`ign_plan` FR, `basemap_at` AT. It reaches the client on the picker row as
`data-basemap-countries`, and `boundaryCountryCodes()` (`static/js/map.js`)
reads it off the row carrying `aria-checked="true"`.

**Why.** Two reasons, and the second is the one that decides it.

The rows had stopped meaning what they filtered. They were SNOW-172's
**country** toggles; SNOW-658 relabelled them as bulletin **providers**
without narrowing what `applyCountryFilters` applied them to. So unticking
every provider fed the deliberate always-false expression to all seven
region layers at once, and the map drew nothing at all — Major, Minor and
Micro still ticked, no outlines anywhere. The help copy had already made
the split: a provider row "takes its bulletins off the map", while
Boundaries are "outlines of the European avalanche regions".

A country scope is still worth having, and it belongs to the basemap
because **the basemap is what runs out at the border**. `swisstopo`,
`ign_plan` and `basemap_at` render blank outside their own country, so an
outline drawn past it delineates ground no tile covers. Binding the two
together makes the outlines describe exactly the map underneath them.
Per-basemap extent is already a modelled concept here — `BASE_LAYER_BANDS`
sizes the offline base layer the same way
([the-base-layer-band-follows-the-basemap-extent](the-base-layer-band-follows-the-basemap-extent.md)).

**Consequences.**

- **Italy is reachable only through OpenFreeMap.** There is no Italian
  basemap — South Tyrol and Trentino publish raster WMTS only — so the
  Italian outlines are drawn on the global style and nowhere else. The
  Italian *bulletins* are unaffected: ALBINA's choropleth paints on any
  basemap, because the fill is never scoped by the basemap.
- **A missing or unrecognised `data-basemap-countries` falls back to all
  four codes**, in `map.js` and in `map_layer_sync_status.js` alike. The
  safe direction is drawing outlines nobody asked for; the unsafe one is
  the blank map this ruling exists to fix.
- **A new basemap must declare its coverage.** `BASEMAP_COUNTRIES` is
  validated at import exactly as `BASEMAP` is — a missing key, or a code
  outside `MAP_COUNTRY_CODES`, fails the boot rather than silently drawing
  no outlines.
- **A cold open on the global basemap loads four countries, not one.**
  The boundary tiers are per-country server-side (`/api/regions.geojson`
  and friends **require** a `?country=`), and geometry that was never
  fetched draws no outline however the filter reads — so boot walks the
  union of the enabled providers' countries and the basemap's, and a
  `snowdesk:basemap-changed` listener loads whatever a swap newly needs.
  `ensureCountryLoaded` is one unit — geometry *and* that country's season
  ratings — so this costs three extra countries' feeds (~40 KB of ratings
  each). All of it is off the critical path (which stays CH-L4 + ratings +
  resorts), publicly cacheable, and lands in Cache Storage, which is what
  makes the outlines readable offline. Splitting the ratings leg out would
  mean tracking geometry-loaded and ratings-loaded separately inside a
  function whose failure path does a group-atomic revert; measure before
  taking that on.
- **A country can now be loaded before its provider row is switched on.**
  `ensureCountryLoaded` short-circuits on `loadedCountries`, and its
  choropleth paint is part of the load it skips, so the toggle that
  reveals the country runs the ratings leg on its own
  (`loadCountryRatings`) — otherwise those regions sit grey until the next
  date change.
- **The `l1` / `l2` / `l4` sync dots probe the basemap's countries.** They
  report whether a tier is available offline, and the tier is only fetched
  for the countries it is drawn for. `map_layer_sync_status.js` listens for
  `snowdesk:basemap-changed` for the same reason: without it a swap leaves
  a green dot over geometry that was never fetched.
