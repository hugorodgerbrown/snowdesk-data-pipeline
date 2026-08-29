/*
 * tests/js/test_map_edit_locations_core.js — Vitest unit tests for
 * static/js/map_edit_locations_core.js (SNOW-755).
 *
 * The location editor's logic was extracted into a ``*_core.js`` module
 * precisely so it could be tested here rather than in a browser: the mode
 * machine, the catalogue filtering, the request-payload shapes and the
 * link-picker's exclusion of already-linked resorts are all pure functions
 * of their arguments, and none of them needs a map to assert.
 *
 * ``map_edit_locations_core.js`` is a browser IIFE with no exports —
 * importing it for side effects publishes ``self.pwaEditLocationsCore``.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/map_edit_locations_core.js';

const core = self.pwaEditLocationsCore;
const MODE = core.MODE;

/** A resort catalogue entry, as the queue endpoint serves it. */
const resort = (id, name) => ({ id, name, region_id: 'CH-4115', region_name: 'Martigny / Verbier', latitude: 46.1, longitude: 7.2 });

/** A location payload, as every endpoint answers with. */
const location = (id, name, links = []) => ({
  id,
  uuid: `uuid-${id}`,
  name,
  kind: 'PEAK',
  latitude: 46.10361,
  longitude: 7.29889,
  elevation_m: null,
  links,
});

/** One resort-to-location link inside a location payload. */
const link = (id, resortId, resortName, role = 'TOP') => ({
  id,
  resort_id: resortId,
  resort_name: resortName,
  region_id: 'CH-4115',
  role,
  is_primary: false,
});

describe('reduceMode', () => {
  it('starts a create from any mode', () => {
    // "New location" is always available — it is how the operator adds a
    // summit without first finding an unrelated row to leave.
    for (const from of [MODE.IDLE, MODE.EDIT, MODE.LINK, MODE.CREATE]) {
      expect(core.reduceMode(from, 'new')).toBe(MODE.CREATE);
    }
  });

  it('leaves create mode when a catalogue row is picked', () => {
    expect(core.reduceMode(MODE.CREATE, 'select')).toBe(MODE.EDIT);
  });

  it('toggles between edit and link', () => {
    expect(core.reduceMode(MODE.EDIT, 'link')).toBe(MODE.LINK);
    expect(core.reduceMode(MODE.LINK, 'link')).toBe(MODE.EDIT);
  });

  it('refuses to enter link mode with nothing selected', () => {
    // There is no location to link, and the create form carries its own
    // first link — so the button is inert rather than misleading.
    expect(core.reduceMode(MODE.IDLE, 'link')).toBe(MODE.IDLE);
    expect(core.reduceMode(MODE.CREATE, 'link')).toBe(MODE.CREATE);
  });

  it('unwinds one step on cancel', () => {
    // Backing out of a link returns to the location being edited, which
    // is where the operator was.
    expect(core.reduceMode(MODE.LINK, 'cancel')).toBe(MODE.EDIT);
    expect(core.reduceMode(MODE.CREATE, 'cancel')).toBe(MODE.IDLE);
    expect(core.reduceMode(MODE.EDIT, 'cancel')).toBe(MODE.EDIT);
  });

  it('goes idle when the selection is cleared', () => {
    expect(core.reduceMode(MODE.LINK, 'clear')).toBe(MODE.IDLE);
  });

  it('ignores an unknown event', () => {
    expect(core.reduceMode(MODE.EDIT, 'wombat')).toBe(MODE.EDIT);
    expect(core.reduceMode(undefined, 'wombat')).toBe(MODE.IDLE);
  });
});

describe('visibleSections', () => {
  it('shows exactly one block per mode', () => {
    // Two forms on screen would mean two Save buttons writing to two
    // endpoints, with nothing saying which is which.
    for (const mode of [MODE.IDLE, MODE.CREATE, MODE.EDIT, MODE.LINK]) {
      const shown = Object.values(core.visibleSections(mode)).filter(Boolean);
      expect(shown.length).toBeLessThanOrEqual(1);
    }
    expect(core.visibleSections(MODE.CREATE)).toEqual({ create: true, edit: false, link: false });
    expect(core.visibleSections(MODE.LINK)).toEqual({ create: false, edit: false, link: true });
  });

  it('shows nothing at all when idle', () => {
    expect(core.visibleSections(MODE.IDLE)).toEqual({ create: false, edit: false, link: false });
  });
});

describe('filterLocations', () => {
  const rows = [
    location(1, 'Mont Fort', [link(10, 100, 'Verbier'), link(11, 101, 'Nendaz')]),
    location(2, 'Attelas', [link(12, 100, 'Verbier')]),
    location(3, 'Corvatsch', [link(13, 102, 'Silvaplana')]),
  ];

  it('returns everything for a blank query', () => {
    expect(core.filterLocations(rows, '   ')).toHaveLength(3);
  });

  it('matches on the place name, case-insensitively', () => {
    expect(core.filterLocations(rows, 'attel').map((r) => r.name)).toEqual(['Attelas']);
  });

  it('matches on a linked resort name', () => {
    // Curation runs resort by resort — "show me everything Verbier
    // reaches" is the question being asked, and no summit's own name
    // contains its resort's.
    expect(core.filterLocations(rows, 'verbier').map((r) => r.name)).toEqual([
      'Mont Fort',
      'Attelas',
    ]);
  });

  it('does not mutate the input', () => {
    const original = rows.slice();
    core.filterLocations(rows, 'verbier');
    expect(rows).toEqual(original);
  });

  it('tolerates a location with no links', () => {
    expect(core.filterLocations([location(4, 'Orphan')], 'orphan')).toHaveLength(1);
  });
});

describe('sortLocations / upsertLocation', () => {
  it('sorts by name, then id', () => {
    const sorted = core.sortLocations([location(2, 'Mont Fort'), location(1, 'Attelas')]);
    expect(sorted.map((r) => r.name)).toEqual(['Attelas', 'Mont Fort']);
  });

  it('inserts a created row where a reload would put it', () => {
    const rows = [location(1, 'Attelas'), location(2, 'Corvatsch')];
    const after = core.upsertLocation(rows, location(3, 'Bella Tola'));
    expect(after.map((r) => r.name)).toEqual(['Attelas', 'Bella Tola', 'Corvatsch']);
  });

  it('replaces a saved row rather than duplicating it', () => {
    const rows = [location(1, 'Mont Fort')];
    const after = core.upsertLocation(rows, location(1, 'Mont Fort (summit)'));
    expect(after).toHaveLength(1);
    expect(after[0].name).toBe('Mont Fort (summit)');
  });

  it('replaces on an unlink, so the dropped link disappears', () => {
    // Unlink answers with the same location payload as every other write,
    // one link shorter — the catalogue has to take it the same way.
    const rows = [location(1, 'Mont Fort', [link(10, 100, 'Verbier'), link(11, 101, 'Nendaz')])];
    const after = core.upsertLocation(rows, location(1, 'Mont Fort', [link(10, 100, 'Verbier')]));
    expect(after[0].links.map((l) => l.resort_name)).toEqual(['Verbier']);
  });

  it('ignores a payload with no id', () => {
    const rows = [location(1, 'Mont Fort')];
    expect(core.upsertLocation(rows, null)).toEqual(rows);
  });
});

describe('linkableResorts', () => {
  const resorts = [resort(100, 'Verbier'), resort(101, 'Nendaz'), resort(102, 'Thyon')];

  it('drops the resorts already linked', () => {
    // Otherwise the duplicate-link 400 is one click away from an operator
    // who cannot see, from the picker alone, what is already attached.
    const mont_fort = location(1, 'Mont Fort', [link(10, 100, 'Verbier')]);
    expect(core.linkableResorts(mont_fort, resorts).map((r) => r.name)).toEqual([
      'Nendaz',
      'Thyon',
    ]);
  });

  it('offers everything for a location with no links', () => {
    expect(core.linkableResorts(location(1, 'Orphan'), resorts)).toHaveLength(3);
  });

  it('offers everything when nothing is selected', () => {
    expect(core.linkableResorts(null, resorts)).toHaveLength(3);
  });
});

describe('missingForCreate / missingForLink', () => {
  it('names every outstanding requirement', () => {
    expect(core.missingForCreate({})).toEqual(['name', 'pin', 'resort', 'role']);
  });

  it('is satisfied by a full draft', () => {
    expect(
      core.missingForCreate({ name: 'Mont Fort', hasPin: true, resortId: 100, role: 'TOP' }),
    ).toEqual([]);
  });

  it('does not require a kind', () => {
    // An unclassified place is a legitimate row, in the model and in the
    // sheet — a location minted from user data has neither name nor kind.
    expect(
      core.missingForCreate({ name: 'Somewhere', hasPin: true, resortId: 100, role: 'MID', kind: '' }),
    ).toEqual([]);
  });

  it('treats a whitespace-only name as missing', () => {
    const missing = core.missingForCreate({ name: '   ', hasPin: true, resortId: 1, role: 'TOP' });
    expect(missing).toEqual(['name']);
  });

  it('does not ask a link for a pin', () => {
    // The location already knows where it is; linking says only that
    // another resort reaches it.
    expect(core.missingForLink({ resortId: 101, role: 'TOP' })).toEqual([]);
    expect(core.missingForLink({})).toEqual(['resort', 'role']);
  });
});

describe('payload builders', () => {
  it('carries kind and role independently on a create', () => {
    // Location.kind describes the place; ResortLocation.role describes one
    // resort's relationship to it. A point can be one resort's top and
    // another's mid-station, so neither may be derived from the other.
    const body = core.createPayload({
      name: '  Mont Fort  ',
      kind: 'PEAK',
      lat: 46.10361,
      lon: 7.29889,
      resortId: 100,
      role: 'MID',
      isPrimary: true,
    });
    expect(body).toEqual({
      name: 'Mont Fort',
      kind: 'PEAK',
      latitude: 46.10361,
      longitude: 7.29889,
      resort_id: 100,
      role: 'MID',
      is_primary: true,
    });
  });

  it('rounds a placed pin to the precision the sheet carries', () => {
    // Storing more than five decimals would make every import_locations
    // run report an update that is only trailing digits.
    const body = core.createPayload({ name: 'X', lat: 46.1036123456, lon: 7.2988987654 });
    expect(body.latitude).toBe(46.10361);
    expect(body.longitude).toBe(7.2989);
  });

  it('sends no link fields on a save', () => {
    const body = core.savePayload({ name: 'Mont Fort', kind: 'PEAK', lat: 46.1, lon: 7.3 });
    expect(Object.keys(body).sort()).toEqual(['kind', 'latitude', 'longitude', 'name']);
  });

  it('sends no coordinate on a link', () => {
    const body = core.linkPayload({ resortId: 101, role: 'TOP' });
    expect(body).toEqual({ resort_id: 101, role: 'TOP', is_primary: false });
  });
});

describe('parseLatLon', () => {
  it('reads the shape Google Maps shows above a search result', () => {
    expect(core.parseLatLon('46.431918, 6.978587')).toEqual({ lat: 46.431918, lon: 6.978587 });
  });

  it('tolerates whitespace and a degrees sign', () => {
    expect(core.parseLatLon(' 46.43°  6.97° ')).toEqual({ lat: 46.43, lon: 6.97 });
  });

  it('rejects anything that is not two numbers', () => {
    for (const bad of ['', null, '46.43', 'up a bit', '1 2 3', '200, 7']) {
      expect(core.parseLatLon(bad)).toBeNull();
    }
  });

  it('leaves the Swiss bbox to the server', () => {
    // A pin just outside Switzerland is a mistake worth an explanation,
    // not a silently ignored keystroke.
    expect(core.parseLatLon('56.797, -5.003')).toEqual({ lat: 56.797, lon: -5.003 });
  });
});

describe('formatCoord', () => {
  it('shows five decimals, matching what the server stores', () => {
    expect(core.formatCoord(46.1036123, 7.2988987)).toBe('46.10361, 7.29890');
  });

  it('reads as empty rather than as zero when there is no pin', () => {
    expect(core.formatCoord(null, null)).toBe('—');
    expect(core.formatCoord(46.1, undefined)).toBe('—');
  });
});

describe('labelFor', () => {
  const kinds = [
    { value: 'PEAK', label: 'Peak' },
    { value: 'VILLAGE', label: 'Village' },
  ];

  it('reads the label the server served', () => {
    expect(core.labelFor(kinds, 'VILLAGE')).toBe('Village');
  });

  it('falls back to the raw value rather than to blank', () => {
    // A blank cell reads as missing data; the code at least says what it
    // did not recognise.
    expect(core.labelFor(kinds, 'MID')).toBe('MID');
    expect(core.labelFor([], '')).toBe('');
  });
});

describe('the export', () => {
  let frozen;

  beforeEach(() => {
    frozen = self.pwaEditLocationsCore;
  });

  it('is frozen, like every other *_core module', () => {
    expect(Object.isFrozen(frozen)).toBe(true);
  });
});
