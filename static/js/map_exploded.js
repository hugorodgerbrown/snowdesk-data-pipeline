/* Interactive explanation of the live MapLibre layer stack. Every texture is
 * captured from the application's renderer; nothing is traced or recoloured. */
(() => {
  'use strict';
  if (!window.snowdeskLayerExplainer) return;

  const fallbackStrings = {
    title: 'How the map is built',
    loading: 'Loading map layers…',
    close: 'Close layer animation',
    'view-label': 'Map layer view',
    stacked: 'Stacked',
    exploded: 'Exploded',
    replay: 'Replay build',
    'map-layers': 'Map layers',
    'layers-order': 'Layers from top to bottom',
    reset: 'Reset',
    'data-credits': 'Data credits',
    swisstopo: 'Swisstopo',
    'winter-basemap': 'Winter basemap',
    slope: 'Slope angle',
    'terrain-shading': 'Terrain shading',
    bulletins: 'SLF bulletins',
    'avalanche-danger': 'Avalanche danger',
    major: 'L1 · Major',
    minor: 'L2 · Minor',
    micro: 'L4 · Micro',
    boundaries: 'EAWS boundaries',
    resorts: 'Resorts',
    'resort-locations': 'Resort locations',
    locations: 'Your locations',
    'saved-places': 'Saved places',
    'stacked-status': 'Stacked layer view.',
    'exploded-status': 'Exploded layer view.',
    'replay-status': 'Replaying map build.',
    complete: 'Every layer, together in one map.',
    restored: 'All layers restored.',
    adding: 'Adding %(name)s.',
    unavailable: '%(name)s is unavailable. Close and retry after its data loads.',
    timeout: 'Map tiles did not finish loading. Close and retry.',
    'no-date': 'No bulletin date selected',
  };
  const strings = window.pwaStrings?.read('map-explainer-strings', fallbackStrings)
    || fallbackStrings;
  const interpolate = window.pwaStrings?.interpolate
    || ((value, values) => value.replace(/%\((\w+)\)s/g, (match, key) => values[key] ?? match));
  const escape = window.pwaStrings?.escapeHtml
    || (value => String(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;',
    })[character]));
  const text = key => escape(strings[key]);

  const dialog = document.createElement('dialog');
  dialog.className = 'map-exploded';
  dialog.setAttribute('aria-labelledby', 'exploded-title');
  dialog.innerHTML = `
    <header class="exploded-header">
      <h1 id="exploded-title">${text('title')}</h1>
      <p id="exploded-status" class="exploded-status" role="status">${text('loading')}</p>
      <button type="button" class="exploded-close" data-close aria-label="${text('close')}">×</button>
    </header>
    <div class="exploded-controls">
      <div class="exploded-segments" role="group" aria-label="${text('view-label')}">
        <button type="button" data-view="stacked" aria-pressed="true" disabled>${text('stacked')}</button>
        <button type="button" data-view="exploded" aria-pressed="false" disabled>${text('exploded')}</button>
      </div>
      <button type="button" class="exploded-replay" data-replay disabled>
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.2 7.1A6.5 6.5 0 1 1 3.7 12M4.2 7.1V2.8M4.2 7.1h4.3"/></svg>
        ${text('replay')}
      </button>
    </div>
    <div class="exploded-scene">
      <div class="exploded-stage"><svg role="img" aria-label="${text('map-layers')}"></svg></div>
      <aside class="exploded-sidebar" aria-label="${text('layers-order')}">
        <div class="exploded-reset-slot"><button type="button" class="exploded-reset" data-reset hidden>${text('reset')}</button></div>
        <div class="exploded-labels"></div>
      </aside>
    </div>
    <footer>
      <span><span data-date></span> · © swisstopo · SLF / EAWS</span>
      <a href="/colophon/">${text('data-credits')}</a>
    </footer>`;
  document.body.appendChild(dialog);

  const status = dialog.querySelector('[role="status"]');
  const stage = dialog.querySelector('.exploded-stage');
  const svg = dialog.querySelector('.exploded-stage svg');
  const labels = dialog.querySelector('.exploded-labels');
  const reset = dialog.querySelector('[data-reset]');
  const replay = dialog.querySelector('[data-replay]');
  const viewButtons = Array.from(dialog.querySelectorAll('[data-view]'));
  const ns = 'http://www.w3.org/2000/svg';
  const captureAbort = new AbortController();
  let amount = 0;
  let shown = 0;
  let activeIndex = -1;
  let animation = 0;
  let busy = true;
  let closed = false;

  const mapEl = document.getElementById('map');
  const groups = [
    [strings.swisstopo, strings['winter-basemap'], [], null],
  ];
  // SNOW-691/724: the slope raster is behind an operator kill switch — clear
  // settings.SLOPE_TILE_URL and `installSlopeLayer` never adds `slope-raster`,
  // so the template omits `data-slope-tile-url`. Demonstrating a sheet the map
  // will not install aborts the build on its missing-layer check, taking every
  // later layer with it; omit the group instead.
  if (mapEl?.dataset.slopeTileUrl) {
    groups.push([strings.slope, strings['terrain-shading'], ['slope-raster'], null]);
  }
  groups.push(
    [strings.bulletins, strings['avalanche-danger'], ['regions-fill', 'bulletin-groupings-line'], 'l3'],
    [strings.major, strings.boundaries, ['major-regions-line', 'major-regions-label'], 'l1'],
    [strings.minor, strings.boundaries, ['sub-regions-line', 'sub-regions-label'], 'l2'],
    [strings.micro, strings.boundaries, ['regions-line', 'regions-label'], null],
    [strings.resorts, strings['resort-locations'], ['resorts-pin', 'resorts-label'], 'resorts'],
  );
  if (mapEl?.dataset.favouritesEligible === 'true') {
    groups.push([strings.locations, strings['saved-places'], ['favourites-pin', 'favourites-label'], 'favourites']);
  }

  const textures = [];
  groups.forEach(([name, description], index) => {
    const group = document.createElementNS(ns, 'g');
    const rect = document.createElementNS(ns, 'rect');
    rect.setAttribute('width', '1000');
    rect.setAttribute('height', '720');
    rect.setAttribute('class', 'exploded-sheet');
    const image = document.createElementNS(ns, 'image');
    image.setAttribute('width', '1000');
    image.setAttribute('height', '720');
    image.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    group.append(rect, image);
    svg.appendChild(group);

    const row = document.createElement('div');
    row.className = 'exploded-label';
    row.dataset.built = 'false';
    // The basemap is the one rung that cannot be switched off.
    if (index === 0) row.dataset.fixed = 'true';
    const control = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('role', 'switch');
    input.checked = true;
    input.disabled = true;
    input.setAttribute('aria-label', name);
    const copy = document.createElement('span');
    copy.className = 'exploded-label-copy';
    const title = document.createElement('strong');
    title.textContent = name;
    const subtitle = document.createElement('span');
    subtitle.textContent = description;
    copy.append(title, subtitle);
    const off = document.createElement('span');
    off.className = 'exploded-label-off';
    off.textContent = 'OFF';
    off.setAttribute('aria-hidden', 'true');
    control.append(input, copy, off);
    row.appendChild(control);
    // Prepended, so the list always reads top of the ladder down to the
    // basemap — the order the exploded view draws, and the order the stack
    // really has. It used to be built basemap-first and flipped with
    // `column-reverse` for the exploded view only, so switching views turned
    // the list upside down, and in the stacked view the basemap sat at the
    // top of a ladder it is the bottom of. Reversing the DOM rather than the
    // flex direction also keeps reading and tab order matching what is drawn.
    labels.prepend(row);

    const texture = {
      group,
      image,
      row,
      input,
      off,
      name,
      enabled: true,
      fixed: index === 0,
    };
    input.addEventListener('change', () => {
      texture.enabled = input.checked;
      syncRows();
      draw();
    });
    textures.push(texture);
  });

  function stopAnimation() {
    cancelAnimationFrame(animation);
  }

  function syncRows() {
    textures.forEach((texture, index) => {
      const built = index < shown;
      texture.row.dataset.built = String(built);
      texture.row.dataset.current = String(index === activeIndex);
      texture.row.dataset.off = String(built && !texture.enabled);
      texture.input.checked = texture.enabled;
      texture.input.disabled = busy || !built || texture.fixed;
      texture.off.hidden = !built || texture.enabled;
    });
    reset.hidden = !textures.some(texture => !texture.fixed && !texture.enabled);
    replay.disabled = busy || shown < textures.length;
    viewButtons.forEach(button => { button.disabled = busy || shown < textures.length; });
  }

  function draw() {
    const width = stage.clientWidth;
    if (!width) return;
    const narrow = width < 600;
    const available = Math.max(120, width - 2);
    const k = available / 1000;
    const flatScale = available / 1000;
    const layerStep = narrow ? 34 : 60;
    const explodedTop = narrow ? 18 : 24;
    const explodedBaseY = explodedTop + (textures.length - 1) * layerStep;
    const explodedA = .86 * k;
    const explodedB = .035 * k;
    const explodedC = -.14 * k;
    const explodedD = .12 * k;
    const explodedSheetHeight = 1000 * explodedB + 720 * explodedD;
    const explodedHeight = explodedBaseY + explodedSheetHeight + (narrow ? 18 : 26);
    const flatHeight = flatScale * 720 + 2;
    const height = explodedHeight * amount + flatHeight * (1 - amount);
    stage.style.height = `${height}px`;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

    textures.forEach((texture, index) => {
      const flat = 1 - amount;
      const a = explodedA * amount + flatScale * flat;
      const b = explodedB * amount;
      const c = explodedC * amount;
      const d = explodedD * amount + flatScale * flat;
      const x = 112 * k * amount + flat;
      const y = (explodedBaseY - index * layerStep) * amount + flat;
      texture.group.setAttribute('transform', `matrix(${a},${b},${c},${d},${x},${y})`);
      texture.group.style.opacity = index < shown && texture.enabled ? 1 : 0;
    });
    syncRows();
  }

  function setPressedView(view) {
    dialog.dataset.view = view;
    viewButtons.forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.view === view));
    });
  }

  function move(target, announce = true) {
    stopAnimation();
    const from = amount;
    const start = performance.now();
    const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1300;
    function tick(now) {
      const progress = duration ? Math.min(1, (now - start) / duration) : 1;
      const eased = progress * progress * (3 - 2 * progress);
      amount = from + (target - from) * eased;
      draw();
      if (progress < 1) animation = requestAnimationFrame(tick);
      else if (announce) status.textContent = target
        ? strings['exploded-status']
        : strings['stacked-status'];
    }
    animation = requestAnimationFrame(tick);
  }

  viewButtons.forEach(button => {
    button.addEventListener('click', () => {
      const view = button.dataset.view;
      setPressedView(view);
      move(view === 'exploded' ? 1 : 0);
    });
  });

  function waitForFrame(map, event, read = () => undefined) {
    return new Promise((resolve, reject) => {
      if (captureAbort.signal.aborted) {
        reject(new Error('Capture cancelled'));
        return;
      }
      const cleanup = () => {
        clearTimeout(timer);
        map.off(event, done);
        captureAbort.signal.removeEventListener('abort', cancel);
      };
      const cancel = () => {
        cleanup();
        reject(new Error('Capture cancelled'));
      };
      const done = () => {
        cleanup();
        try { resolve(read()); } catch (error) { reject(error); }
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error(strings.timeout));
      }, 45000);
      captureAbort.signal.addEventListener('abort', cancel, {once: true});
      map.once(event, done);
      map.triggerRepaint();
    });
  }

  function waitForDelay(delay) {
    if (delay <= 0) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const done = () => {
        captureAbort.signal.removeEventListener('abort', cancel);
        resolve();
      };
      const cancel = () => {
        clearTimeout(timer);
        reject(new Error('Capture cancelled'));
      };
      const timer = setTimeout(done, delay);
      captureAbort.signal.addEventListener('abort', cancel, {once: true});
    });
  }

  function waitForCadence(lastRevealAt) {
    if (!lastRevealAt) return Promise.resolve();
    return waitForDelay(Math.max(0, 1100 - (performance.now() - lastRevealAt)));
  }

  async function replayBuild() {
    if (busy || shown < textures.length) return;
    busy = true;
    activeIndex = 0;
    shown = 0;
    setPressedView('stacked');
    move(0, false);
    status.textContent = strings['replay-status'];
    draw();
    let lastRevealAt = performance.now();
    for (let index = 0; index < textures.length; index += 1) {
      activeIndex = index;
      syncRows();
      await waitForCadence(lastRevealAt);
      if (closed) return;
      shown = index + 1;
      lastRevealAt = performance.now();
      draw();
    }
    activeIndex = -1;
    busy = false;
    status.textContent = strings.complete;
    draw();
  }

  replay.addEventListener('click', () => {
    replayBuild().catch(error => {
      if (!closed) {
        status.textContent = error.message;
        status.setAttribute('role', 'alert');
      }
    });
  });

  reset.addEventListener('click', () => {
    textures.forEach(texture => { texture.enabled = true; });
    status.textContent = strings.restored;
    draw();
  });

  const idle = map => waitForFrame(map, 'idle');
  const capture = map => waitForFrame(map, 'render', () => map.getCanvas().toDataURL('image/png'));

  async function build() {
    await window.snowdeskMapState.ready;
    const map = window.snowdeskMap;
    const oldCamera = {
      center: map.getCenter(), zoom: map.getZoom(),
      bearing: map.getBearing(), pitch: map.getPitch(),
    };
    const visibility = new Map();
    const filters = new Map();
    // Snapshot every layer the demo is about to override, and do it LAZILY.
    // Reading the style once after ``ready`` misses the tiers the boot-time
    // lazy loads install later — the bulletin boundary, and an eligible user's
    // default-on favourites — which `prepare()` brings in mid-build. Those
    // layers would be absent from the snapshot, and the restore below would
    // read them as `none` and leave them hidden for the rest of the session,
    // with `overlayLoaded` already true so no loader reinstates them. Taking
    // each layer's state the first time we see it records what the
    // application configured, before this demo touches it.
    const remember = () => {
      for (const layer of map.getStyle().layers) {
        if (visibility.has(layer.id)) continue;
        visibility.set(layer.id, layer.layout?.visibility || 'visible');
        filters.set(layer.id, layer.filter || null);
      }
    };
    // Show exactly ``ids`` and nothing else, recording what each layer was
    // configured to be first.
    const only = (ids) => {
      remember();
      for (const layer of map.getStyle().layers) {
        map.setLayoutProperty(layer.id, 'visibility', ids.includes(layer.id) ? 'visible' : 'none');
      }
    };
    remember();
    const oldFill = map.getPaintProperty('regions-fill', 'fill-opacity');
    const container = map.getContainer();
    const oldContainerStyle = container.getAttribute('style');
    // SNOW-172: the capture flies to a fixed Swiss view, but the persisted
    // country filters are the user's. A returning visitor who follows only
    // France or ALBINA has Switzerland filtered out, so the region, bulletin,
    // L1, L2 and L4 sheets would all capture blank. Force CH on for the
    // duration and put the preference back afterwards — localStorage is never
    // written.
    let restoreCountries = () => {};
    try {
      const metadata = await window.snowdeskLayerExplainer.prepare();
      if (closed) return;
      remember();
      restoreCountries = await window.snowdeskLayerExplainer.focusSwitzerland();
      // Install every tier the ladder shows BEFORE photographing anything.
      // The demo is a fixed sequence — basemap, then each layer on top — and
      // it must read the same whatever the visitor happens to have switched
      // on, so it cannot wait for the ordinary lazy loads to arrive as their
      // step comes round. That is also a correctness fix: the boot-time
      // ``restoreOverlay`` calls for a visitor's OWN enabled tiers are
      // fire-and-forget, still in flight when ``ready`` resolves, so an L1
      // outline or a resort pin would install a moment after a step had
      // already hidden everything and land in that step's photograph. The
      // Swisstopo sheet came out with region boundaries and resorts drawn on
      // it. Loading is not showing: each tier installs at the visibility its
      // own toggle dictates, and the capture forces what it needs per step.
      for (const [, , , loaderKey] of groups) {
        if (loaderKey) await window.snowdeskLayerExplainer.prepare(loaderKey);
      }
      remember();
      const applicationSources = new Set([
        'regions', 'major-regions', 'sub-regions', 'bulletin-groupings',
        'resorts', 'favourites', 'weather', 'routes', 'community-reports',
        'cached-tiles', 'slope',
      ]);
      groups[0][2] = map.getStyle().layers
        .filter(layer => !applicationSources.has(layer.source) && !/^(routes|trip|download|cached)-/.test(layer.id))
        .map(layer => layer.id);
      Object.assign(container.style, {
        position: 'fixed', left: '-10000px', top: '0', width: '1000px', height: '720px',
      });
      map.resize();
      map.fitBounds([[5.9, 45.72], [10.6, 47.85]], {padding: 45, duration: 0});
      map.setPaintProperty('regions-fill', 'fill-opacity', metadata.fillOpacity);
      let lastRevealAt = 0;
      for (let index = 0; index < groups.length; index += 1) {
        const [name, , ids] = groups[index];
        if (closed) return;
        activeIndex = index;
        status.textContent = interpolate(strings.adding, {name});
        syncRows();
        if (!ids.some(id => map.getLayer(id))) {
          throw new Error(interpolate(strings.unavailable, {name}));
        }
        only(ids);
        await idle(map);
        // Re-assert immediately before the shutter. The preload above settles
        // the tiers this demo owns, but an overlay it does not — community
        // reports, weather, routes — can still land from its own boot restore
        // during the wait, and would otherwise be photographed.
        only(ids);
        const url = await capture(map);
        const decoded = new Image();
        decoded.src = url;
        await decoded.decode();
        textures[index].image.setAttribute('href', url);
        await waitForCadence(lastRevealAt);
        shown = index + 1;
        lastRevealAt = performance.now();
        draw();
      }
      dialog.querySelector('[data-date]').textContent = metadata.date || strings['no-date'];
      activeIndex = -1;
      busy = false;
      status.textContent = strings.complete;
      draw();
    } finally {
      for (const layer of map.getStyle().layers) {
        // A layer this demo never saw is a layer it never hid; leave it alone
        // rather than defaulting it to `none`.
        if (!visibility.has(layer.id)) continue;
        map.setLayoutProperty(layer.id, 'visibility', visibility.get(layer.id));
        if (layer.type !== 'background' && layer.type !== 'raster') {
          map.setFilter(layer.id, filters.get(layer.id));
        }
      }
      // Last, so the recomposed country filters win over the snapshot above.
      restoreCountries();
      map.setPaintProperty('regions-fill', 'fill-opacity', oldFill);
      if (oldContainerStyle === null) container.removeAttribute('style');
      else container.setAttribute('style', oldContainerStyle);
      map.resize();
      map.jumpTo(oldCamera);
    }
  }

  const observer = new ResizeObserver(draw);

  /*
   * Take ``layers=exploded`` back off the URL when the demo closes.
   *
   * The parameter is what opened the demo, and it is what map.js reads to
   * force the Swiss winter basemap for the capture. Left in place, closing
   * the dialog dismisses the demo for this paint only — a reload, a
   * back/forward step, or a shared link reopens it and re-forces the
   * basemap over the visitor's own stored choice. Closing is the visitor
   * saying they are done, so the address bar has to agree. replaceState
   * rather than pushState: the demo is not a history entry of its own, and
   * every other parameter (``d``, the date) stays exactly as it was.
   */
  function clearExplainerParam() {
    const url = new URL(window.location.href);
    if (url.searchParams.get('layers') !== 'exploded') return;
    url.searchParams.delete('layers');
    window.history.replaceState(window.history.state, '', url.href);
  }

  dialog.addEventListener('close', () => {
    closed = true;
    captureAbort.abort();
    stopAnimation();
    observer.disconnect();
    clearExplainerParam();
  });
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  syncRows();
  setPressedView('stacked');
  dialog.showModal();
  draw();
  observer.observe(stage);
  build().catch(error => {
    if (!closed) {
      busy = false;
      activeIndex = -1;
      status.textContent = error.message;
      status.setAttribute('role', 'alert');
      syncRows();
    }
  });
})();
