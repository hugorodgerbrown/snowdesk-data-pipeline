/*
 * static/js/map_drop_zone.js — "Drop zone": a one-tap offline download of
 * the ground around wherever the user is standing.
 *
 * HACK-MODE PROTOTYPE (feature discovery). It is deliberately built out of
 * the pieces the two shipped download controls already share rather than
 * new machinery: `pwaBasemapDownloadCore.buildBlob` sizes the area,
 * `runPinnedDownload` (static/js/map_basemap_downloads.js) runs it, and a
 * successful run is recorded as an ordinary custom area
 * (`_appendCustomArea`), so it lists, renames, syncs to the account and
 * evicts exactly like a framed one. Nothing downstream knows a drop zone
 * from a framed box.
 *
 * What it adds is the WAY IN. The framing overlay asks the user to choose
 * an area: open the downloads sheet, tap "Download a custom area", pan and
 * zoom a reticle over the ground they want, then confirm. That is the right
 * interaction at a desk the night before. It is the wrong one in a car park
 * with gloves on and one bar of signal, where the area the user wants is
 * "here, and far enough around here to matter" and they want it now.
 *
 * So: tap the LOCATE roundel — the one that was already there — and the fix
 * it takes drops a dot on your position, draws a circle around it and arms
 * the roundel as "download this". Tap it again and the drop runs.
 *
 * ONE ROUNDEL, TWO MODES, and the reason is that the position is a
 * one-shot fix rather than a live track (`trackUserLocation: false`,
 * map_geolocate.js): the circle does not follow you, so a second roundel
 * asking the device for the same fix a moment after the first is two
 * controls for one thought. `data-mode` on `#locate-toggle` says which mode
 * it is in, CSS swaps the glyph, and the arm expires after ARM_MS so a user
 * who only wanted to be found gets their locate button back without having
 * to dismiss anything.
 *
 * This module never calls the Geolocation API itself — map_geolocate.js is
 * the single owner of it, and it now broadcasts the pill's own fix
 * (`snowdesk:geolocate`, `source: 'pill'`) the way it already broadcast the
 * field report's.
 *
 * THE MAP IS THE RADIUS CONTROL. The circle holds a fixed share of the
 * viewport (RETICLE_FRACTION), so zooming out grows the ground it covers
 * and zooming in shrinks it — pinch on a phone, scroll on a desktop, both
 * of them gestures the user already makes on this map for other reasons.
 * That is the same regime the framing overlay's reticle is in below its
 * ceiling (map_custom_download.js's `_updateSelection`), reached without
 * its own vocabulary of buttons. An earlier pass here had a −/+ ladder of
 * fixed radii instead; it worked, but it asked the user to learn a second
 * way to make a map bigger and smaller, next to the one they were already
 * using. The readout says what the current circle costs, live, before
 * anything is fetched.
 *
 * The circle is anchored to the FIX, not to the viewport centre: panning
 * moves it about the screen (and off it) rather than re-aiming it, because
 * this control only ever downloads around where the user actually is. If
 * they want to choose somewhere else, the framing overlay is that control.
 *
 * Circle vs box: the circle is what is DRAWN, because a radius is how
 * "around me" is actually thought about. What is FETCHED is the circle's
 * bounding box — tiles are a square grid, and clipping to the circle would
 * save a corner's worth of tiles at the cost of a tile-set that no longer
 * matches any bbox the rest of the download estate understands. Worth
 * revisiting if a drop zone ever becomes a real feature; for discovery the
 * over-fetch is a rounding error against the ceiling.
 *
 * LOAD ORDER: runs at parse time, binds DOM from _map_embed.html, and must
 * load after map.js (it reads `MAP`) and after map_geolocate.js (whose
 * listener answers the locate request it dispatches). It sits OUTSIDE the
 * map bundle — see tests/public/test_map_script_order.py — so its tag goes
 * below the bundle's run in home.html, not inside it.
 */

(function mapDropZoneInit() {
  // The locate roundel IS this control's roundel (SNOW-XXX / hack mode). See
  // this module's header: one tap finds you, and the same button then means
  // "download the ground around me" until it times out.
  const btn = document.getElementById('locate-toggle');
  const overlayEl = document.getElementById('map-drop-zone-overlay');
  const readoutEl = document.getElementById('map-drop-zone-readout');
  const confirmBtn = document.getElementById('map-drop-zone-confirm');
  const cancelBtn = document.getElementById('map-drop-zone-cancel');
  const hintEl = document.getElementById('map-drop-zone-hint');
  if (!btn || !overlayEl || !readoutEl || !confirmBtn || !cancelBtn) return;
  if (typeof MAP === 'undefined' || !MAP) return;

  // SNOW-620 / bin/i18n-lint: every user-facing string is rendered by the
  // server into #map-drop-zone-strings-template and read back here, with the
  // English literal as the fallback. Its own template id rather than a key
  // inside map-strings-template, because tests/test_js_strings_are_
  // translatable.py maps each template to the ONE module that reads it.
  const STRINGS = self.pwaStrings.read('map-drop-zone-strings-template', {
    readout: '%(km)s km around you · up to %(mb)s MB',
    'readout-capped': '%(km)s km around you · up to %(mb)s MB (as big as one drop goes)',
    busy: '%(pct)s · %(mb)s',
    done: '%(mb)s downloaded around you',
    error: 'Download failed — try again',
    offline: 'Offline — reconnect to drop',
    hint: 'Zoom the map to change how far this reaches',
    'area-name': 'Drop zone %(n)s',
    'action-close': 'Close',
    'roundel-locate': 'Locate me',
    'roundel-drop': 'Download the area around you',
  });

  // How much of the viewport the circle spans: its radius is this share of
  // the SHORTER side, so it reads as a circle on a phone in portrait and on
  // a desktop in landscape alike, and always leaves room for the CTA bar
  // and the control stacks it must not sit under.
  const RETICLE_FRACTION = 0.34;

  // How long the roundel stays armed as "download this" with nothing
  // happening. Long enough to read the size, look at what the circle
  // covers and decide; short enough that a user who only wanted to be
  // found has their locate button back before they next reach for it. The
  // timer restarts on every interaction (`resetArmTimer`), so this bounds
  // inactivity, not the whole interaction.
  const ARM_MS = 12000;

  // How long a finished download's "23.4 MB downloaded around you" stays up
  // before the surface clears itself away. A beat to read a short sentence,
  // not a notification the user has to dismiss.
  const DONE_MS = 3500;

  // How long a confirmed drop will wait for the map's style to settle
  // before running anyway (and being refused by the runner's own check).
  // See `styleSettled`.
  //
  // Generous, because every second of it is better spent waiting than
  // failing: the run cannot start at all until the style can be read for
  // its tile sources, so a wait that ends early converts a download the
  // user asked for into an error they have to understand. The camera ease
  // this control starts is 600ms, and the tiles for the new zoom follow it.
  const STYLE_SETTLE_MS = 8000;

  const CANCEL_LABEL_DEFAULT = cancelBtn.textContent;
  const CIRCLE_SOURCE = 'drop-zone-circle';
  const CIRCLE_FILL_LAYER = 'drop-zone-circle-fill';
  const CIRCLE_LINE_LAYER = 'drop-zone-circle-line';
  // Marks the map while a drop zone is drawn, so CSS can take MapLibre's
  // own accuracy circle off the screen for as long as ours is on it — two
  // translucent discs about the same point, one meaning "roughly where you
  // are" and the other "exactly what will be downloaded", is a picture with
  // no reading. The accuracy circle earns its place during the locate step
  // and only there.
  const DROP_OPEN_CLASS = 'map-drop-zone-open';

  let centre = null; // {lat, lon} — the fix this drop is anchored to.
  let pending = null; // {bbox, blob, radiusKm, capped} — see `recompute`.
  let runState = 'idle';
  // The pending "revert to locate" timer, or null when the roundel is not
  // armed. See `resetArmTimer`.
  let armTimer = null;
  // The pending "the download is finished, clear up" timer. See `paintRun`'s
  // 'done' branch.
  let doneTimer = null;

  /**
   * Whether a download may be started right now.
   *
   * Mirrors the other download controls: a user-forced offline mode counts
   * as offline, because the worker refuses the run either way.
   *
   * @returns {boolean}
   */
  function networkInUse() {
    const connectivity = window.pwaConnectivity;
    return connectivity ? connectivity.isOnline() : navigator.onLine !== false;
  }

  /**
   * A closed ring approximating a circle of `radiusKm` about `lat`/`lon`.
   *
   * Plain equirectangular offsets: at the radii on offer here (≤ 40 km, in
   * the Alps) the error against a geodesic circle is metres, and the ring is
   * decoration for a bbox that is itself rounded out to whole tiles.
   *
   * @param {number} lat
   * @param {number} lon
   * @param {number} radiusKm
   * @param {number} [steps] Vertices; 96 is smooth at every zoom this map
   *   reaches without being worth thinking about.
   * @returns {number[][]} `[[lon, lat], …]`, first point repeated last.
   */
  function circleRing(lat, lon, radiusKm, steps) {
    const n = steps || 96;
    const latDelta = radiusKm / 111.32;
    const lonDelta = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
    const ring = [];
    for (let i = 0; i <= n; i++) {
      const theta = (i / n) * 2 * Math.PI;
      ring.push([lon + lonDelta * Math.cos(theta), lat + latDelta * Math.sin(theta)]);
    }
    return ring;
  }

  /**
   * The circle's bounding box — what is actually downloaded.
   *
   * @param {number} lat
   * @param {number} lon
   * @param {number} radiusKm
   * @returns {[number, number, number, number]} `[west, south, east, north]`.
   */
  function circleBBox(lat, lon, radiusKm) {
    const latDelta = radiusKm / 111.32;
    const lonDelta = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
    return [lon - lonDelta, lat - latDelta, lon + lonDelta, lat + latDelta];
  }

  /**
   * Draw (or move) the circle on the map.
   *
   * Idempotent: the source is added once and `setData` thereafter, so a
   * radius change is a data update rather than a layer teardown.
   *
   * @returns {void}
   */
  function paintCircle() {
    if (!centre || !pending) return;
    const { lat, lon } = centre;
    const data = {
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [circleRing(lat, lon, pending.radiusKm)],
      },
      properties: {},
    };
    const colour = dropZoneColour();
    const existing = MAP.getSource(CIRCLE_SOURCE);
    if (existing) {
      // The centre never moves within one drop, so only the ring is
      // re-serialised as the radius changes. The colour is re-applied
      // rather than assumed: a basemap swap changes it, and the layers may
      // well have survived (see the basemap-changed listener).
      existing.setData(data);
      MAP.setPaintProperty(CIRCLE_FILL_LAYER, 'fill-color', colour);
      MAP.setPaintProperty(CIRCLE_LINE_LAYER, 'line-color', colour);
      return;
    }
    MAP.addSource(CIRCLE_SOURCE, { type: 'geojson', data: data });
    MAP.addLayer({
      id: CIRCLE_FILL_LAYER,
      type: 'fill',
      source: CIRCLE_SOURCE,
      // Fainter than the accuracy circle it replaces on screen: this one is
      // about the ground UNDER it, which the user is reading to decide.
      paint: { 'fill-color': colour, 'fill-opacity': 0.1 },
    });
    MAP.addLayer({
      id: CIRCLE_LINE_LAYER,
      type: 'line',
      source: CIRCLE_SOURCE,
      // Dashed, and that is the whole visual argument against the accuracy
      // circle: a solid disc is a measurement of where you are, a dashed
      // outline is a selection you made and can still change. The two must
      // not be told apart by colour alone, because the colour moves with
      // the basemap.
      paint: {
        'line-color': colour,
        'line-width': 2,
        'line-opacity': 0.95,
        'line-dasharray': [3, 2],
      },
    });
    // No centre dot of its own: the locate step already put a beacon on the
    // exact point — MapLibre's user-location dot for a real fix, the
    // `.map-fake-beacon` marker for a spoofed one — and a second dot under
    // it would be one marker too many for one place. An earlier pass drew
    // one because at the time nothing else marked the centre.
  }

  /**
   * Remove the circle and its layers.
   *
   * @returns {void}
   */
  function clearCircle() {
    [CIRCLE_LINE_LAYER, CIRCLE_FILL_LAYER].forEach((id) => {
      if (MAP.getLayer(id)) MAP.removeLayer(id);
    });
    if (MAP.getSource(CIRCLE_SOURCE)) MAP.removeSource(CIRCLE_SOURCE);
  }

  /**
   * The colour this drop zone draws in: the active basemap's own identity
   * colour (`--color-basemap-*`, resolved by `basemapIdentityColour`).
   *
   * The same colour the downloads sheet, the progress grid and the layers
   * menu use for that basemap, so a drop zone reads as belonging to the map
   * under it — rust on swisstopo winter, blue on openfreemap — rather than
   * as one more blue thing next to the geolocation dot.
   *
   * @returns {string} A CSS colour, literal by the time MapLibre sees it.
   */
  function dropZoneColour() {
    return basemapIdentityColour(activeBasemapKey());
  }

  /**
   * The reticle's radius in screen pixels: a fixed share of the shorter
   * viewport side, so the circle is the same size on screen at every zoom
   * and it is the GROUND under it that changes.
   *
   * Read from the map container on every call rather than cached — a phone
   * rotating, a desktop window resizing and the browser's own chrome
   * appearing all change it, and a cached value would leave the circle
   * claiming a radius it no longer draws.
   *
   * @returns {number} Pixels.
   */
  function reticleRadiusPx() {
    const rect = MAP.getContainer().getBoundingClientRect();
    return Math.min(rect.width, rect.height) * RETICLE_FRACTION;
  }

  /**
   * What the reticle currently spans on the ground, in kilometres.
   *
   * Measured by unprojecting two points the reticle's width apart rather
   * than from a metres-per-pixel formula: MapLibre owns the projection,
   * including whatever pitch and bearing the user has left the map in, and
   * asking it is both shorter and correct in cases a constant is not.
   *
   * @returns {number} Kilometres.
   */
  function reticleRadiusKm() {
    const centreScreen = MAP.project([centre.lon, centre.lat]);
    const edge = MAP.unproject([centreScreen.x + reticleRadiusPx(), centreScreen.y]);
    // Equirectangular, to match circleRing/circleBBox — the three have to
    // agree with each other more than they have to be geodesic.
    const dLat = (edge.lat - centre.lat) * 111.32;
    const dLon = (edge.lng - centre.lon) * 111.32 * Math.cos((centre.lat * Math.PI) / 180);
    return Math.sqrt(dLat * dLat + dLon * dLon);
  }

  /**
   * Size the drop from the map's current zoom, capping it at the download
   * ceiling.
   *
   * `budgetScaleForBBox` answers "how far can this box be scaled about its
   * centre and still fit" — for a circle that is the radius factor, so a
   * zoom level the ceiling cannot afford yields the largest radius it can,
   * flagged `capped`. The circle is then drawn at the CAPPED radius, not
   * the reticle's: zooming further out stops growing it, which is the same
   * lock the framing overlay's frame has at its own ceiling, and it is
   * visible rather than a number quietly going red.
   *
   * @returns {void}
   */
  function recompute() {
    const core = self.pwaBasemapDownloadCore;
    if (!core || !centre) {
      pending = null;
      return;
    }
    const [minZ, maxZ] = core.MICRO_BAND;
    const sources = core.tileSourceCount(activeBasemapTileSources(MAP));
    const asked = Math.max(0.1, reticleRadiusKm());
    // The ceiling is the DEVICE's, not a constant: how big a drop may be is
    // a question about what this phone can hold.
    const ceilingMb = basemapDeviceCeilingMb();
    // SNOW-868: sized at the price the readout below charges the same
    // circle at, or every drop reads as over the ceiling on a national
    // basemap — see `budgetScaleForBBox`'s own note.
    const bytesPerTile = core.bytesPerTileForSources(activeBasemapTileSources(MAP));
    const scale = core.budgetScaleForBBox(
      circleBBox(centre.lat, centre.lon, asked),
      minZ,
      maxZ,
      sources,
      ceilingMb,
      bytesPerTile
    );
    const radiusKm = scale < 1 ? asked * scale : asked;
    pending = {
      // The bbox is still what gets STORED and what the manage sheet zooms
      // to — "where is this area" is a rectangle's job. What gets FETCHED
      // is the circle: `circleBlob` clips each tile row to it, so the
      // download stops taking the corners the user did not ask for and the
      // downloaded-tiles overlay (which draws the real cached tiles, not
      // this bbox) stops drawing a square around a circular selection.
      bbox: circleBBox(centre.lat, centre.lon, radiusKm),
      blob: core.circleBlob(centre.lat, centre.lon, radiusKm, minZ, maxZ, ceilingMb),
      radiusKm: radiusKm,
      // `budgetScaleForBBox` sizes against the BOUNDING BOX, so the cap it
      // returns is conservative for a circle by about a fifth — the tiles
      // actually fetched are ~pi/4 of the box it priced. Deliberate: the
      // alternative is inverting a circular tile count, which is a step
      // function of where the circle sits on the grid and would make the
      // ring shimmer as the map moves (see `budgetScaleForBBox`'s own note
      // on why it is closed-form).
      capped: scale < 1,
    };
  }

  /**
   * Format a radius for the readout — whole kilometres above 1 km, one
   * decimal below, so a capped radius never reads as "0 km".
   *
   * @param {number} km
   * @returns {string}
   */
  function formatKm(km) {
    return km >= 10 ? String(Math.round(km)) : String(Math.round(km * 10) / 10);
  }

  /**
   * Repaint the readout and the buttons from `pending`.
   *
   * @returns {void}
   */
  function renderIdle() {
    if (runState === 'busy' || runState === 'done') return;
    if (!pending) return;
    const core = self.pwaBasemapDownloadCore;
    const mb = core.sourceScaledMb(
      pending.blob.mb,
      activeBasemapTileSources(MAP),
      pending.blob.count
    );
    readoutEl.textContent = self.pwaStrings.interpolate(
      STRINGS[pending.capped ? 'readout-capped' : 'readout'],
      { km: formatKm(pending.radiusKm), mb: mb }
    );
    if (hintEl) hintEl.textContent = STRINGS.hint;
    confirmBtn.disabled = !networkInUse();
  }

  /**
   * Paint one run state onto the overlay — the shape `runPinnedDownload`
   * calls, matching the framing overlay's own `paintRun`.
   *
   * @param {string} state 'idle' | 'busy' | 'done' | 'error' | 'offline'.
   * @param {number} [pct] Tiles done as a whole percentage, while busy.
   * @param {number} [bytes] On-disk bytes so far.
   * @returns {void}
   */
  function paintRun(state, pct, bytes) {
    runState = state;
    overlayEl.dataset.runState = state;
    // `formatMegabytes`, matching the framing overlay's `_formatBytes` —
    // the manage core is optional there and is treated as optional here.
    const manage = self.pwaBasemapManageCore;
    const asMb = (n) =>
      manage && typeof manage.formatMegabytes === 'function' ? manage.formatMegabytes(n || 0) : '0 MB';
    if (state === 'busy') {
      confirmBtn.disabled = true;
      // The hint stops being true the moment the run starts — zooming no
      // longer changes anything, because the selection is locked to what
      // was confirmed (see `refresh`).
      if (hintEl) hintEl.textContent = '';
      // `pct` arrives as a whole percentage (0–100), not a fraction — the
      // runner's own units, matching the framing overlay's readout.
      readoutEl.textContent = self.pwaStrings.interpolate(STRINGS.busy, {
        pct: `${pct || 0}%`,
        mb: asMb(bytes),
      });
      return;
    }
    if (state === 'done') {
      confirmBtn.style.display = 'none';
      cancelBtn.textContent = STRINGS['action-close'];
      readoutEl.textContent = self.pwaStrings.interpolate(STRINGS.done, { mb: asMb(bytes) });
      // The download is what the user came for, so the surface goes away
      // once it has said so — long enough to read the size, then the halo,
      // the footer and the roundel's second verb all clear together. Close
      // is still there for anyone who would rather dismiss it themselves.
      clearTimeout(doneTimer);
      doneTimer = setTimeout(() => {
        if (runState === 'done') closeDrop();
      }, DONE_MS);
      return;
    }
    if (state === 'error') {
      readoutEl.textContent = STRINGS.error;
      confirmBtn.disabled = !networkInUse();
      return;
    }
    if (state === 'offline') {
      readoutEl.textContent = STRINGS.offline;
      confirmBtn.disabled = true;
      return;
    }
    renderIdle();
  }

  /**
   * Open the overlay on a fresh fix: reset every per-drop piece of state,
   * draw the circle, and frame it.
   *
   * @param {{lat: number, lon: number}} fix
   * @returns {void}
   */
  function openDrop() {
    if (!centre) return;
    // What the device can hold may have changed since the last drop — the
    // user has been browsing, and other areas may have been deleted.
    // Re-resolved here and repainted when it lands; `recompute` below uses
    // the previous answer in the meantime, which is right often enough that
    // waiting on a storage estimate before drawing anything would be the
    // worse trade.
    refreshBasemapDeviceCeiling().then(() => refresh());
    runState = 'idle';
    confirmBtn.style.removeProperty('display');
    cancelBtn.textContent = CANCEL_LABEL_DEFAULT;
    overlayEl.hidden = false;
    overlayEl.dataset.runState = 'idle';
    // No camera move. An earlier pass eased the map to the zoom at which
    // the circle meant a round 10 km, and it read as the map lurching for
    // no reason the user had asked for — they had just been taken to their
    // own position, and being moved again a tap later loses the view they
    // were reading. The radius is simply what the CURRENT zoom implies;
    // zooming is how it changes, which is the same gesture either way.
    MAP.getContainer().classList.add(DROP_OPEN_CLASS);
    recompute();
    paintCircle();
    renderIdle();
    // The arm's expiry measured a user who tapped locate and then did
    // nothing. Once the footer is up they are plainly deciding, and it
    // carries an explicit Cancel — so the timer stops here rather than
    // pulling the surface out from under them mid-decision.
    clearTimeout(armTimer);
    armTimer = null;
  }

  /**
   * Close the overlay and take the circle off the map.
   *
   * @returns {void}
   */
  function closeDrop() {
    clearTimeout(doneTimer);
    doneTimer = null;
    MAP.getContainer().classList.remove(DROP_OPEN_CLASS);
    overlayEl.hidden = true;
    clearCircle();
    centre = null;
    pending = null;
    runState = 'idle';
    disarmRoundel();
  }

  /**
   * Put the roundel into "download this" mode and start the clock on it.
   *
   * `data-mode` is read by two other places, which is why it is an
   * attribute rather than a local: CSS swaps the glyph off it, and
   * map_geolocate.js's own click handler stands down while it says 'drop'
   * (see the guard in its `locate`).
   *
   * @returns {void}
   */
  function armRoundel() {
    btn.dataset.mode = 'drop';
    btn.setAttribute('aria-label', STRINGS['roundel-drop']);
    btn.setAttribute('title', STRINGS['roundel-drop']);
    resetArmTimer();
  }

  /**
   * Give the roundel back to "locate me".
   *
   * @returns {void}
   */
  function disarmRoundel() {
    clearTimeout(armTimer);
    armTimer = null;
    btn.dataset.mode = 'locate';
    btn.setAttribute('aria-label', STRINGS['roundel-locate']);
    btn.setAttribute('title', STRINGS['roundel-locate']);
  }

  /**
   * Start (or restart) the armed roundel's expiry.
   *
   * This covers ONE state: located, offered a download, and not yet asked
   * for one. A user who tapped locate to see where they were should get
   * their locate button back rather than keep a second verb they did not
   * ask for. Once the footer is open the timer stops — `openDrop` clears
   * it — because from there the user is deciding and Cancel is the exit.
   *
   * @returns {void}
   */
  function resetArmTimer() {
    clearTimeout(armTimer);
    armTimer = setTimeout(() => {
      if (runState === 'busy' || runState === 'done') return;
      closeDrop();
    }, ARM_MS);
  }

  /**
   * Resolve once the map's style is loaded, or after a short grace period.
   *
   * @returns {Promise<void>} Always resolves — never rejects, and never
   *   waits indefinitely.
   */
  function styleSettled() {
    return new Promise((resolve) => {
      if (typeof MAP.isStyleLoaded !== 'function' || MAP.isStyleLoaded()) {
        resolve();
        return;
      }
      let done = false;
      const settle = () => {
        if (done) return;
        done = true;
        resolve();
      };
      MAP.once('idle', settle);
      setTimeout(settle, STYLE_SETTLE_MS);
    });
  }

  /**
   * Run the drop: hand the built blob to the shared pinned-download runner
   * and record a successful run as an ordinary custom area.
   *
   * A near-copy of the framing overlay's `handleConfirm` — deliberately, for
   * a prototype: the two should be reconciled into one call site if this
   * becomes a real feature, and copying it is the cheapest way to find out
   * whether the interaction is worth that work.
   *
   * @returns {Promise<void>}
   */
  async function handleConfirm() {
    if (!pending || pending.blob.over_ceiling || runState === 'busy') return;
    if (!networkInUse()) {
      paintRun('offline');
      return;
    }
    const blob = pending.blob;
    const bbox = pending.bbox;
    const core = self.pwaBasemapDownloadCore;
    // Wait for the style before starting. `activeBasemapTileSources` is
    // gated on `MAP.isStyleLoaded()`, and the runner treats "no tile
    // sources" as a failed download — correctly, since a run with no tiles
    // to fetch would paint 'done' over an area that is not available
    // offline. This control walks straight into that: the tap that arms it
    // also eases the camera to the drop, so the second tap can easily land
    // while the new zoom's tiles are still loading and the style is dirty.
    // The framing overlay is exposed to the same thing and gets away with
    // it because framing takes seconds of panning first.
    //
    // A beat's wait rather than a disabled button: the user asked for this
    // download, and "wait a moment, then run it" is what they meant. Bounded,
    // because a style that never settles must still reach an answer — the
    // runner's own check is what turns that into an error.
    //
    await styleSettled();
    const areaId = core ? core.generateCustomAreaId() : '';

    await runPinnedDownload({
      areaId: areaId,
      mb: blob.mb,
      // SNOW-868: the tile count the per-basemap price multiplies — see the
      // runner's own `count` note.
      count: blob.count,
      paint: (nextState, pct, bytes) => paintRun(nextState, pct, bytes),
      loadBlob: () => blob,
      finish: async (
        result,
        runBlob,
        { core, progressFill, tileSources, basemapKey, renderDeps, content },
      ) => {
        const cancelled = !!(result && result.cancelled);
        const ok = core.downloadSucceeded(result);
        if (cancelled) {
          await progressFill.finish(false);
          paintRun(networkInUse() ? 'idle' : 'offline');
        } else if (ok) {
          const ordinal = await _nextCustomAreaOrdinal();
          const area = {
            id: areaId,
            ordinal: ordinal,
            // SNOW-XXX: what KIND of user-made area this is. A drop zone
            // is a circle around where the user was standing; a framed
            // custom area is a box they drew. The downloads panel names
            // the kind in words on every row, and the id cannot tell them
            // apart — both mint a `custom-` id, because both live in the
            // same record and the same bucket family.
            type: 'dropzone',
            // A drop zone names ITSELF, unlike a framed area (whose default
            // label is derived at read time from `ordinal`). Prototype
            // shortcut, and a real one to revisit: writing the name freezes
            // the language it was dropped in, which is exactly what
            // `default-custom-name` exists to avoid.
            name: self.pwaStrings.interpolate(STRINGS['area-name'], { n: ordinal }),
            bbox: bbox,
            band: runBlob.band,
            centre_tile: runBlob.centre_tile,
            template: tileSources,
            basemapKey: basemapKey || null,
            deps: Array.isArray(renderDeps) ? renderDeps : [],
            bytes: Number(result.bytes) || 0,
            // SNOW-924: as map_custom_download.js — a drop zone records
            // an ordinary custom area, so it carries the content stamp
            // on the same terms.
            ...(content && content.total > 0 && content.ok === content.total
              ? { contentAt: new Date().toISOString() }
              : {}),
            savedAt: new Date().toISOString(),
          };
          await _appendCustomArea(area);
          await progressFill.finish(true);
          paintRun('done', undefined, area.bytes);
        } else {
          await progressFill.finish(false);
          paintRun('error');
          revealBasemapDownloadError(result ? result.reason : null);
        }
        // Same post-run refresh both shipped controls do: the layers menu is
        // a live cache-state dashboard, and the cached-tiles overlay should
        // show what just landed without waiting for a basemap swap.
        window.pwaLayerSyncStatus?.refresh();
        window.pwaDownloadedOverlay?.refresh();
        // The device holds this run's bytes now, so the next drop's ceiling
        // is lower by that much.
        refreshBasemapDeviceCeiling();
      },
    });
  }

  /**
   * Re-derive the selection from the camera and repaint it.
   *
   * Called on every map movement while a drop is open, which is what makes
   * the zoom the radius control. Frozen once a run starts: from 'busy'
   * onwards the selection is what was confirmed, and a zoom must not move
   * the ground out from under tiles already being fetched (the framing
   * overlay freezes the gestures themselves for the same reason; here the
   * map stays live and it is the SELECTION that is pinned, so a user can
   * still look around while their drop downloads).
   *
   * @returns {void}
   */
  function refresh() {
    // `overlayEl.hidden` is the check that makes this a THREE-step control
    // rather than a two-step one. `centre` is set the moment the locate fix
    // lands, and this runs on every map movement — including the camera
    // ease that same fix starts — so without it the halo was drawn on the
    // first tap, before the user had asked for a drop at all. It read as
    // the locate control announcing how badly it had placed them.
    if (!centre || overlayEl.hidden || runState === 'busy' || runState === 'done') return;
    recompute();
    paintCircle();
    renderIdle();
  }

  // The roundel's SECOND tap. Its first is map_geolocate.js's, which stands
  // down while `data-mode` says 'drop' — so exactly one of the two handlers
  // acts on any given tap.
  btn.addEventListener('click', () => {
    // Only in 'drop' mode — map_geolocate.js owns the tap in 'locate' mode
    // and stands down here (see the guard in its `locate`).
    if (btn.dataset.mode !== 'drop') return;
    // A run in flight owns the roundel: the footer's own Cancel is how a
    // download is stopped, not a stray tap on the button that started it.
    if (runState === 'busy' || runState === 'done') return;
    // Toggle, matching every other roundel that opens a surface: this tap
    // draws the halo and the footer, and tapping it again takes them away
    // exactly as Cancel does.
    if (overlayEl.hidden) openDrop();
    else closeDrop();
  });

  // The fix the locate roundel just took. `source` is what distinguishes it
  // from the field report's own request, which asks for coordinates and
  // must not open a drop zone over the form it is filling in.
  document.addEventListener('snowdesk:geolocate', (event) => {
    const detail = event.detail || {};
    if (detail.source !== 'pill') return;
    if (typeof detail.lat !== 'number' || typeof detail.lon !== 'number') return;
    if (runState === 'busy') return;
    // The fix ARMS the roundel and nothing else. Locating and downloading
    // are two decisions, and a tap that means the first must not commit the
    // user to seeing the second — the first version of this drew the halo
    // and threw up the size footer on every locate, which is an answer to a
    // question the user had not asked yet.
    centre = { lat: detail.lat, lon: detail.lon };
    armRoundel();
  });

  confirmBtn.addEventListener('click', () => handleConfirm());
  // 'move' covers pan, zoom, pitch and rotate, and MapLibre already fires
  // it once per rendered frame — so this is the same "once per animation
  // frame while the selection is changing" budget the framing overlay
  // recomputes on, without a rAF loop of our own.
  MAP.on('move', refresh);
  cancelBtn.addEventListener('click', () => {
    // Mid-run, Cancel stops the download as well as closing — matching the
    // framing overlay, where a cancel that only hid the sheet left the run
    // going unseen.
    if (runState === 'busy') window.pwaWarmCacheCancel?.();
    closeDrop();
  });

  // A basemap swap tears every layer off the style (see row_focus.js's own
  // note), so a circle drawn before the swap is gone after it. Redraw it if
  // a drop is still open — a control whose state survives a basemap change
  // only because nothing has changed the basemap yet is a bug waiting for a
  // user to find it.
  document.addEventListener('snowdesk:basemap-changed', () => {
    // `refresh` re-adds the source and layers as a side effect of
    // `paintCircle` — after a style swap there is nothing left to update in
    // place, so the same call that tracks a zoom also rebuilds the circle.
    // It also re-sizes the drop: a basemap with two vector sources costs
    // twice the ground, so the ceiling may now cap a radius it did not.
    if (!overlayEl.hidden) refresh();
  });

  // Going offline mid-decision disables the button rather than failing the
  // tap; coming back enables it.
  document.addEventListener('snowdesk:connectivity-changed', () => {
    if (!overlayEl.hidden && runState !== 'busy' && runState !== 'done') renderIdle();
  });
})();
