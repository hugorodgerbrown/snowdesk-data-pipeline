/*
 * static/js/map_day_notes.js — the day's notes, behind a bell in the map's
 * date row (SNOW-1206).
 *
 * Some things are true of the day on the map rather than of anything on
 * it: no bulletin covers it, or the weather layer's forecast does not
 * reach it. SNOW-1195 and SNOW-1196 said each as a pill of its own beside
 * the date control. The pills read as standing chrome, said too little to
 * earn the room, and on a tablet pushed the row under the bottom-right
 * roundel. Hugo: this sort of date-dependent metadata belongs behind an
 * ephemeral notification icon.
 *
 * So the row carries one bell, #map-day-notes-toggle:
 *
 *   - SHOWN ONLY WHEN THE DAY HAS A NOTE. No notes, no bell — nothing on
 *     the row stands for an empty list.
 *   - A DOT FOR WHAT IS NEW. The dot shows when the notes differ from the
 *     ones last opened, and clears when the card opens. Stepping to a day
 *     with the same notes brings no dot back; a day with different ones
 *     does.
 *   - THE CARD OPENS BESIDE IT. #map-day-notes-card, above the bell on a
 *     desktop, under it on a phone (map_calendar.js moves the bell into
 *     the top row with the date control). It registers with
 *     window.pwaMapOverlays and spares the object sheets, as the calendar
 *     does: a note about the day must not cost the sheet being read.
 *
 * Other modules write the notes; this one only shows them. Each calls
 * ``window.pwaDayNotes.set(id, note)`` with
 * ``{ title, detail, action?: { label, run }, rank }``, or ``null`` to
 * withdraw it. The list sorts by ``rank``. Writers today: map_day_nav.js
 * (no bulletin for the day) and map_weather_window.js (the weather is a
 * forecast that does not reach the day). Both load after this module.
 *
 * Reads ``readUrlDateParam`` / ``formatDatePopup`` (map_shared.js) for the
 * card's heading. Strings from ``map-day-notes-strings-template``.
 */
(function mapDayNotesInit() {
  'use strict';

  const root = document.getElementById('map-day-notes');
  const toggle = document.getElementById('map-day-notes-toggle');
  const card = document.getElementById('map-day-notes-card');
  if (!root || !toggle || !card) return;

  const dot = toggle.querySelector('[data-day-notes-dot]');
  const heading = card.querySelector('[data-day-notes-heading]');
  const list = card.querySelector('[data-day-notes-list]');
  const scrubber = document.getElementById('season-scrubber');

  const STRINGS = self.pwaStrings.read('map-day-notes-strings-template', {
    heading: 'About %(day)s',
  });

  const OVERLAY_NAME = 'day-notes';
  // The selection column's own query (map_overlay_bounds.js): from here a
  // region, place or weather sheet docks down the map's left side.
  const desktop = typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 640px)') : null;

  /** @type {Map<string, { title: string, detail: string, action?: { label: string, run: () => void }, rank: number }>} */
  const notes = new Map();
  const todayKey = scrubber ? scrubber.dataset.today || '' : '';
  let shownKey = '';
  // The notes as they stood when the card last opened.
  let seen = '';

  /** @returns {string} The day the map is showing. */
  function currentKey() {
    return shownKey || readUrlDateParam() || todayKey;
  }

  /** @returns {Array<[string, { title: string, detail: string, action?: { label: string, run: () => void }, rank: number }]>} */
  function sorted() {
    return [...notes].sort((a, b) => (a[1].rank || 0) - (b[1].rank || 0));
  }

  /** @returns {string} What the notes say, to tell new from already seen. */
  function signature() {
    return sorted()
      .map(([id, note]) => `${id}\u0000${note.title}\u0000${note.detail}`)
      .join('\u0001');
  }

  /** @returns {boolean} */
  function isOpen() {
    return !card.hidden;
  }

  /**
   * One note as a list item: its title, its detail, and its press.
   *
   * @param {{ title: string, detail: string, action?: { label: string, run: () => void } }} note
   * @returns {HTMLLIElement}
   */
  function buildItem(note) {
    const item = document.createElement('li');
    item.className = 'map-day-notes-item';
    const title = document.createElement('p');
    title.className = 'map-day-notes-item-title';
    title.textContent = note.title;
    item.append(title);
    if (note.detail) {
      const detail = document.createElement('p');
      detail.className = 'map-day-notes-item-detail';
      detail.textContent = note.detail;
      item.append(detail);
    }
    if (note.action) {
      const { label, run } = note.action;
      const press = document.createElement('button');
      press.type = 'button';
      press.className = 'map-day-notes-action hover-affordance';
      press.textContent = label;
      press.addEventListener('click', () => {
        close();
        run();
      });
      item.append(press);
    }
    return item;
  }

  /** Draw the bell, its dot and the card from the notes. */
  function render() {
    const any = notes.size > 0;
    root.hidden = !any;
    if (!any) {
      close();
      return;
    }
    if (dot) dot.hidden = isOpen() || signature() === seen;
    if (heading) {
      heading.textContent = self.pwaStrings.interpolate(STRINGS.heading, {
        day: formatDatePopup(currentKey()),
      });
    }
    if (list) list.replaceChildren(...sorted().map(([, note]) => buildItem(note)));
    if (isOpen()) seen = signature();
  }

  /**
   * Keep the card clear of a left-hand sheet on a desktop. The bell sits in
   * the date row, which stays put while the selection column covers the
   * stack above it; the card opening up from the bell would land on the
   * sheet, so it starts where the column ends.
   */
  function place() {
    card.style.left = '';
    if (!desktop || !desktop.matches) return;
    const sheet = document.querySelector("[data-sheet-side='left']:not([hidden])");
    if (!sheet) return;
    const shift = sheet.getBoundingClientRect().right + 8 - root.getBoundingClientRect().left;
    if (shift > 0) card.style.left = `${Math.round(shift)}px`;
  }

  /** Open the card, clearing the dot. */
  function open() {
    window.pwaMapOverlays?.opening(OVERLAY_NAME, { spareObjectSheets: true });
    card.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    seen = signature();
    if (dot) dot.hidden = true;
    place();
  }

  /** Close the card. */
  function close() {
    if (card.hidden) return;
    card.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }

  toggle.addEventListener('click', (e) => {
    // The other flyouts' outside-click dismiss must not read this as a
    // press away from them, and ours must not read it as one from us.
    e.stopPropagation();
    if (isOpen()) close();
    else open();
  });
  document.addEventListener('click', (e) => {
    if (isOpen() && e.target instanceof Node && !root.contains(e.target)) close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    toggle.focus();
  });
  document.addEventListener('snowdesk:date-changed', (e) => {
    shownKey = (e.detail && e.detail.date) || '';
    render();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen: isOpen, close: close });

  window.pwaDayNotes = Object.freeze({
    /**
     * Publish a note about the day on the map, or withdraw it with null.
     *
     * @param {string} id
     * @param {?{ title: string, detail: string, action?: { label: string, run: () => void }, rank: number }} note
     */
    set(id, note) {
      if (note) notes.set(id, note);
      else notes.delete(id);
      render();
    },
  });

  render();
})();
