// @ts-check
/**
 * static/js/map_help_mode.js — help mode on the map (SNOW-1119).
 *
 * The "?" roundel turns help mode on and off. While it is on:
 *
 *   - THE MAP SURFACE. Every control on screen has its name drawn beside it,
 *     and a bar at the top says "Tap anything to see what it does", with the
 *     tour one tap away and Done to leave. A tap on a control (or on its
 *     name) does not press it: it rings it and opens a card saying what it
 *     does, with "More in Help" (its /help/ topic) and a button that leaves
 *     help mode and presses the control after all.
 *   - EACH OVERLAY. A tap on the map itself still goes through, so a region,
 *     a resort or saved pin, a weather symbol or a route opens its sheet as
 *     usual — and the sheet is then numbered: a badge on each of its key
 *     parts, and a card keying them. "Got it" puts the guide away for that
 *     sheet until it closes; a sheet already open when help mode starts is
 *     guided at once.
 *
 * Everything a reader sees is rendered by
 * apps/public/templates/public/partials/_map_help_mode.html; this module
 * reads the controls (#map-help-controls) and the guides
 * ([data-sheet-guide]) from it, positions what it draws, and wires it.
 *
 * Why taps are taken in the CAPTURE phase: each control has its own click
 * handler, bound by the module that owns it. Help mode has to see the tap
 * first and stop it there, so no control needs to know help mode exists.
 *
 * Not registered with window.pwaMapOverlays. Help mode is a way of looking
 * at the map, not a surface on it: it has to stay on while the sheets it
 * explains open, and a sheet announcing itself to the registry would
 * otherwise end it. It ends on Done, Escape, the "?" again, "Tour" and a
 * card's own button.
 *
 * Positions are re-read every animation frame while help mode is on: the
 * things it labels move (the "yours" capsule folds, a sheet is dragged
 * between detents and scrolled), and a dozen rectangle reads a frame is
 * cheaper than an observer for each way they can move.
 */

(function mapHelpModeInit() {
  'use strict';

  const root = document.getElementById('map-help-mode');
  const toggleBtn = document.getElementById('map-help-toggle');
  const controlsList = document.getElementById('map-help-controls');
  if (!root || !toggleBtn || !controlsList) return;

  const bar = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-bar'));
  const labelsEl = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-labels'));
  const ring = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-ring'));
  const card = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-card'));
  const cardTitle = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-card-title'));
  const cardBody = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-card-body'));
  const cardMore = /** @type {HTMLAnchorElement} */ (document.getElementById('map-help-mode-card-more'));
  const cardOpen = /** @type {HTMLButtonElement} */ (document.getElementById('map-help-mode-card-open'));
  const badgesEl = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-badges'));
  const guide = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-guide'));
  const guideEyebrow = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-guide-eyebrow'));
  const guideKey = /** @type {HTMLElement} */ (document.getElementById('map-help-mode-guide-key'));
  const guideMore = /** @type {HTMLAnchorElement} */ (document.getElementById('map-help-mode-guide-more'));
  const mapEl = document.getElementById('map');
  const helpUrl = root.dataset.helpUrl || '/help/';

  const MARGIN = 12;
  const RING_PADDING = 4;
  const LABEL_GAP = 8;
  const ROOT_ATTR = 'data-map-help-mode';
  // Clear of the date and search controls along the map's top edge.
  const BAR_OFFSET = 64;
  // A badge is this wide (map.css .map-help-mode-badge), sits this far
  // after the words it numbers, and follows words only on a part at least
  // this wide; anything narrower is a tile or a button and wears it.
  const BADGE_SIZE = 20;
  const BADGE_GAP = 6;
  const BADGE_TEXT_MIN_WIDTH = 64;
  // Text boxes whose tops are this close sit on the same line (a bold run
  // and the plain one after it differ by a pixel or two).
  const LINE_SLOP = 6;

  /**
   * @typedef {{selector: string, label: string, topic: string, open: string,
   *   body: string}} Control
   * @typedef {{key: string, sheet: string, eyebrow: string,
   *   topic: string, more: string, rows: Array<{anchor: string, html: Element}>}} Guide
   */

  /** @type {Control[]} */
  const controls = Array.from(controlsList.querySelectorAll('li[data-control]')).map((li) => {
    const el = /** @type {HTMLElement} */ (li);
    return {
      selector: el.dataset.control || '',
      label: el.dataset.label || '',
      topic: el.dataset.topic || '',
      open: el.dataset.open || '',
      body: (el.textContent || '').replace(/\s+/g, ' ').trim(),
    };
  });

  /** @type {Guide[]} */
  const guides = Array.from(document.querySelectorAll('[data-sheet-guide]')).map((node) => {
    const el = /** @type {HTMLElement} */ (node);
    return {
      key: el.dataset.sheetGuide || '',
      sheet: el.dataset.sheet || '',
      eyebrow: el.dataset.eyebrow || '',
      topic: el.dataset.topic || '',
      more: el.dataset.more || '',
      rows: Array.from(el.querySelectorAll('li[data-anchor]')).map((li) => ({
        anchor: /** @type {HTMLElement} */ (li).dataset.anchor || '',
        html: li,
      })),
    };
  });

  let active = false;
  /** @type {Control|null} */
  let selected = null;
  /** @type {Guide|null} */
  let guided = null;
  // Sheets whose guide was put away with "Got it": not shown again until
  // the sheet has closed.
  const putAway = new Set();
  let frame = 0;
  /** @type {Element|null} */
  let lastFocused = null;

  /**
   * Whether CSS is drawing an element (and it is not hidden).
   *
   * @param {Element|null} el
   * @returns {boolean}
   */
  const isDrawn = (el) => {
    if (!el || el.closest('[hidden]')) return false;
    if (typeof el.checkVisibility === 'function') return el.checkVisibility();
    return getComputedStyle(el).display !== 'none';
  };

  /**
   * The control a tap landed on, by the control itself or its drawn name.
   *
   * @param {EventTarget|null} target
   * @returns {Control|null}
   */
  const controlFor = (target) => {
    if (!(target instanceof Element)) return null;
    const label = target.closest('[data-help-label-for]');
    if (label) {
      const selector = label.getAttribute('data-help-label-for');
      return controls.find((c) => c.selector === selector) || null;
    }
    return controls.find((c) => {
      const el = document.querySelector(c.selector);
      return !!el && el.contains(target);
    }) || null;
  };

  // ---- The names beside the controls ----

  /** @type {Map<string, HTMLElement>} */
  const labelNodes = new Map();

  /**
   * Whether two rectangles overlap.
   *
   * @param {{left: number, top: number, right: number, bottom: number}} a
   * @param {{left: number, top: number, right: number, bottom: number}} b
   * @returns {boolean}
   */
  const overlaps = (a, b) =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

  /**
   * Draw a name for each control on screen, beside it, where it covers
   * neither another control nor another name.
   *
   * The first choice is the side facing the middle of the screen, so a
   * name lands over the map rather than off its edge. Controls sit close
   * together on a desktop (the region chip with its pin and save roundels,
   * the date beside the season scrubber), so a name that would cover a
   * neighbour tries above, then below, then the other side, and keeps the
   * first choice only if every place is taken.
   */
  const placeLabels = () => {
    const vw = window.innerWidth;
    /** @type {Array<{c: Control, el: Element, r: DOMRect}>} */
    const shown = [];
    controls.forEach((c) => {
      const el = document.querySelector(c.selector);
      const node = labelNodes.get(c.selector);
      if (!el || !isDrawn(el)) {
        if (node) node.hidden = true;
        return;
      }
      shown.push({ c, el, r: el.getBoundingClientRect() });
    });
    /** @type {Array<{left: number, top: number, right: number, bottom: number}>} */
    const taken = shown.map((s) => s.r);
    shown.forEach(({ c, r }) => {
      let node = labelNodes.get(c.selector);
      if (!node) {
        node = document.createElement('span');
        node.className = 'map-help-mode-label';
        node.setAttribute('data-help-label-for', c.selector);
        node.textContent = c.label;
        labelsEl.append(node);
        labelNodes.set(c.selector, node);
      }
      node.hidden = false;
      node.dataset.state = selected && selected !== c ? 'dim' : 'on';
      const w = node.offsetWidth;
      const h = node.offsetHeight;
      const midY = r.top + r.height / 2 - h / 2;
      const midX = Math.min(r.left + r.width / 2, r.left + LABEL_GAP * 4) - w / 2;
      const toLeft = { x: r.left - LABEL_GAP - w, y: midY };
      const toRight = { x: r.right + LABEL_GAP, y: midY };
      const facing = r.left + r.width / 2 > vw / 2 ? [toLeft, toRight] : [toRight, toLeft];
      const places = [
        facing[0],
        { x: midX, y: r.top - LABEL_GAP - h },
        { x: midX, y: r.bottom + LABEL_GAP },
        facing[1],
      ].map((at) => {
        const x = Math.max(4, Math.min(at.x, vw - w - 4));
        return { left: x, top: at.y, right: x + w, bottom: at.y + h };
      });
      const free = places.find((box) => !taken.some((t) => overlaps(box, t))) || places[0];
      taken.push(free);
      node.style.left = `${free.left}px`;
      node.style.top = `${free.top}px`;
    });
  };

  // ---- The control card ----

  /**
   * Ring an element.
   *
   * @param {Element} el
   */
  const placeRing = (el) => {
    const r = el.getBoundingClientRect();
    ring.hidden = false;
    ring.style.left = `${r.left - RING_PADDING}px`;
    ring.style.top = `${r.top - RING_PADDING}px`;
    ring.style.width = `${r.width + RING_PADDING * 2}px`;
    ring.style.height = `${r.height + RING_PADDING * 2}px`;
    ring.style.borderRadius = r.width / r.height <= 3 ? '999px' : '14px';
  };

  /**
   * Set the card beside its control: across the screen on a phone, at the
   * control's own height on a desktop, and never over the control.
   *
   * @param {Element} el
   */
  const placeCard = (el) => {
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = card.offsetWidth;
    const h = card.offsetHeight;
    const onRight = r.left + r.width / 2 > vw / 2;
    let left = onRight ? r.left - MARGIN - w : r.right + MARGIN;
    let top = r.top + r.height / 2 - h / 2;
    if (left < MARGIN || left + w > vw - MARGIN) {
      // No room beside it (a phone): above it, or below when it is high up.
      left = Math.max(MARGIN, Math.min(r.left + r.width / 2 - w / 2, vw - w - MARGIN));
      top = r.top - MARGIN - h >= MARGIN ? r.top - MARGIN - h : r.bottom + MARGIN;
    }
    card.style.left = `${left}px`;
    card.style.top = `${Math.max(MARGIN, Math.min(top, vh - h - MARGIN))}px`;
  };

  /**
   * Open the card for a control.
   *
   * @param {Control} c
   */
  const showCard = (c) => {
    selected = c;
    cardTitle.textContent = c.label;
    cardBody.textContent = c.body;
    cardMore.href = c.topic ? `${helpUrl}#${c.topic}` : helpUrl;
    cardOpen.textContent = c.open;
    cardOpen.hidden = !c.open;
    card.hidden = false;
    card.focus();
    paint();
  };

  /** Close the control card, leaving help mode on. */
  const hideCard = () => {
    selected = null;
    card.hidden = true;
    ring.hidden = true;
  };

  // ---- The sheet guides ----

  /**
   * Whether a guide's surface is open now.
   *
   * @param {Guide} g
   * @returns {boolean}
   */
  const guideIsOpen = (g) => isDrawn(document.querySelector(g.sheet));

  /**
   * Fill the guide card for a sheet and show it.
   *
   * @param {Guide} g
   */
  const showGuide = (g) => {
    guided = g;
    hideCard();
    guideEyebrow.textContent = g.eyebrow;
    guideKey.replaceChildren(...g.rows.map((row, i) => {
      const li = document.createElement('li');
      const n = document.createElement('span');
      n.className = 'map-help-mode-badge';
      n.textContent = String(i + 1);
      const text = document.createElement('span');
      text.append(...Array.from(row.html.cloneNode(true).childNodes));
      li.append(n, text);
      return li;
    }));
    guideMore.textContent = g.more;
    guideMore.href = g.topic ? `${helpUrl}#${g.topic}` : helpUrl;
    guide.hidden = false;
    bar.hidden = true;
    raiseSheet(document.querySelector(g.sheet));
  };

  /**
   * Raise a phone sheet resting at peek to half, so the parts the guide
   * numbers are on screen rather than under the fold. Not to full: the
   * guide hangs from the top of the map, and a full sheet would put its
   * first parts under it. Done the way a reader does it — a press on the
   * handle steps up a detent (map_sheet.js).
   *
   * @param {Element|null} sheet
   */
  const raiseSheet = (sheet) => {
    const handle = sheet ? sheet.querySelector('[data-sheet-handle]') : null;
    if (!(sheet instanceof HTMLElement) || !(handle instanceof HTMLElement)) return;
    const detents = (sheet.dataset.detents || '').split(',').map((d) => d.trim());
    if (sheet.dataset.detent === 'peek' && detents.includes('half')) handle.click();
  };

  /** Put the guide away, with its badges. */
  const hideGuide = () => {
    guided = null;
    guide.hidden = true;
    badgesEl.replaceChildren();
    bar.hidden = false;
  };

  /**
   * Number each part of the guided sheet that is on screen, at the part's
   * top-left corner, kept inside the sheet.
   *
   * @param {Guide} g
   */
  const placeBadges = (g) => {
    const sheet = document.querySelector(g.sheet);
    if (!sheet) return;
    const box = sheet.getBoundingClientRect();
    const nodes = /** @type {HTMLElement[]} */ (Array.from(badgesEl.children));
    g.rows.forEach((row, i) => {
      let node = nodes[i];
      if (!node) {
        node = document.createElement('span');
        node.className = 'map-help-mode-badge map-help-mode-badge--on-sheet';
        node.textContent = String(i + 1);
        badgesEl.append(node);
      }
      const part = row.anchor ? sheet.querySelector(row.anchor) : null;
      const r = part && isDrawn(part) ? part.getBoundingClientRect() : null;
      // Off screen when the part is scrolled out of the sheet, or not in
      // this one (a region with no problems that day has no problem card).
      if (!r || r.bottom < box.top || r.top > box.bottom || r.width === 0) {
        node.hidden = true;
        return;
      }
      node.hidden = false;
      const at = badgePoint(/** @type {Element} */ (part), r);
      node.style.left = `${Math.max(box.left + 2, Math.min(at.x, box.right - BADGE_SIZE - 2))}px`;
      node.style.top = `${Math.max(box.top + 2, Math.min(at.y, box.bottom - BADGE_SIZE - 2))}px`;
    });
  };

  /**
   * The end and the middle of the first line of words in an element. Read
   * from its text nodes, not a range over the element: a range's boxes
   * include every element inside it, block-wide, which would put the end
   * of a short line at the far edge of the sheet.
   *
   * @param {Element} el
   * @returns {{right: number, mid: number}|null}
   */
  const firstLine = (el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    /** @type {DOMRect[]} */
    const boxes = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!(node.textContent || '').trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      boxes.push(...Array.from(range.getClientRects()).filter((b) => b.width > 0));
    }
    if (!boxes.length) return null;
    const top = Math.min(...boxes.map((b) => b.top));
    const line = boxes.filter((b) => b.top - top < LINE_SLOP);
    const right = Math.max(...line.map((b) => b.right));
    const bottom = Math.max(...line.map((b) => b.bottom));
    return { right, mid: (top + bottom) / 2 };
  };

  /**
   * Where a part's number goes. After a line of text — the figures, a
   * heading, a breadcrumb — it follows the words, centred on them, so it
   * covers none of them. A small thing with little or no text (a tile, a
   * button, a picture) wears it on its top-left corner.
   *
   * @param {Element} part
   * @param {DOMRect} r
   * @returns {{x: number, y: number}}
   */
  const badgePoint = (part, r) => {
    if (r.width >= BADGE_TEXT_MIN_WIDTH && (part.textContent || '').trim()) {
      const line = firstLine(part);
      if (line) return { x: line.right + BADGE_GAP, y: line.mid - BADGE_SIZE / 2 };
    }
    return { x: r.left - BADGE_SIZE / 2, y: r.top - BADGE_SIZE / 2 };
  };

  // ---- Every frame ----

  /** Re-read every position, and follow sheets opening and closing. */
  const paint = () => {
    if (!active) return;
    // The bar and the guide hang from the top of the map, below the row of
    // controls along its top edge.
    const mapTop = mapEl ? mapEl.getBoundingClientRect().top : 0;
    bar.style.top = `${mapTop + BAR_OFFSET}px`;
    guide.style.top = `${mapTop + MARGIN}px`;
    const open = guides.find(guideIsOpen) || null;
    guides.forEach((g) => {
      if (g !== open) putAway.delete(g.key);
    });
    if (open && open !== guided && !putAway.has(open.key)) showGuide(open);
    if (guided && guided !== open) hideGuide();
    if (guided) placeBadges(guided);

    labelsEl.hidden = !!guided;
    if (!guided) placeLabels();
    if (selected) {
      const el = document.querySelector(selected.selector);
      if (el && isDrawn(el)) {
        placeRing(el);
        placeCard(el);
      } else {
        hideCard();
      }
    }
  };

  const loop = () => {
    paint();
    frame = requestAnimationFrame(loop);
  };

  // ---- On and off ----

  /** Turn help mode on. */
  const enter = () => {
    if (active) return;
    active = true;
    lastFocused = document.activeElement;
    // Help mode looks at the map; nothing else should be floating over it.
    // Sheets are left open — they are what a guide explains.
    window.pwaMapOverlays?.closeAll();
    document.documentElement.setAttribute(ROOT_ATTR, '');
    toggleBtn.setAttribute('aria-pressed', 'true');
    root.hidden = false;
    bar.hidden = false;
    loop();
  };

  /** Turn help mode off, and take everything it drew with it. */
  const leave = () => {
    if (!active) return;
    active = false;
    cancelAnimationFrame(frame);
    hideCard();
    hideGuide();
    putAway.clear();
    labelsEl.replaceChildren();
    labelNodes.clear();
    root.hidden = true;
    document.documentElement.removeAttribute(ROOT_ATTR);
    toggleBtn.setAttribute('aria-pressed', 'false');
    if (lastFocused instanceof HTMLElement && document.contains(lastFocused)) {
      lastFocused.focus();
    }
  };

  // ---- Wiring ----

  toggleBtn.setAttribute('aria-pressed', 'false');

  // Capture phase: help mode sees a tap before the control that owns it.
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target instanceof Element && target.closest('#map-help-toggle')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (active) leave();
      else enter();
      return;
    }
    if (!active) return;
    // A drawn name lives inside help mode's own layer, so it is looked
    // for before that layer's buttons are let through.
    const onName = target instanceof Element && target.closest('[data-help-label-for]');
    if (!onName && target instanceof Element && root.contains(target)) return;
    const c = controlFor(target);
    if (!c) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    showCard(c);
  }, true);

  // A control that acts on pointerdown (a drag handle, the scrubber) would
  // act before the click arrives; stop that too for anything help mode names.
  document.addEventListener('pointerdown', (event) => {
    if (!active) return;
    const target = event.target;
    if (target instanceof Element && root.contains(target)) return;
    if (controlFor(target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  document.getElementById('map-help-mode-done')?.addEventListener('click', leave);
  document.getElementById('map-help-mode-tour')?.addEventListener('click', () => {
    leave();
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-requested'));
  });
  document.getElementById('map-help-mode-card-close')?.addEventListener('click', hideCard);
  cardOpen.addEventListener('click', () => {
    const c = selected;
    leave();
    const el = c ? document.querySelector(c.selector) : null;
    if (el instanceof HTMLElement) el.click();
  });
  const putGuideAway = () => {
    if (guided) putAway.add(guided.key);
    hideGuide();
  };
  document.getElementById('map-help-mode-guide-close')?.addEventListener('click', putGuideAway);
  document.getElementById('map-help-mode-guide-done')?.addEventListener('click', putGuideAway);

  document.addEventListener('keydown', (event) => {
    if (!active || event.key !== 'Escape') return;
    // One step back at a time: the card, then the guide, then help mode.
    if (selected) hideCard();
    else if (guided) putGuideAway();
    else leave();
  });

  // The tour opening (from anywhere) ends help mode: the two would talk
  // over each other about the same controls.
  document.addEventListener('snowdesk:map-help-open', leave);
}());
