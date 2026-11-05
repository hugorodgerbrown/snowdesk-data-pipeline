/*
 * tests/js/test_map_3d_link.js — /map/'s "3D" roundel carries the view across.
 *
 * Pinned: a click rewrites the link to the 2-D camera and the stored
 * switches, and a flat camera takes the 3-D opening pitch.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_3d_core.js';

function fakeMap(pitch) {
  return {
    getCenter: () => ({ lng: 7.5, lat: 46.2 }),
    getZoom: () => 11,
    getPitch: () => pitch,
    getBearing: () => 10,
  };
}

async function boot(pitch, fetchSpy) {
  document.body.innerHTML = '<a id="map-3d-link" href="/map/3d/" data-dem-tile-url="https://t.example/{z}/{x}/{y}.webp">3D</a>';
  window.fetch = fetchSpy || vi.fn(() => Promise.resolve({ ok: true }));
  window.snowdeskMapState = { map: fakeMap(pitch) };
  vi.resetModules();
  await import('../../static/js/map_3d_link.js');
  const link = document.getElementById('map-3d-link');
  link.addEventListener('click', (e) => e.preventDefault());
  link.click();
  return new URL(link.href);
}

describe('map_3d_link', () => {
  beforeEach(() => window.localStorage.clear());

  it('carries the camera, tilting a flat one, and the default switches', async () => {
    const url = await boot(0);
    expect(url.pathname).toBe('/map/3d/');
    expect(url.searchParams.get('at')).toBe(`7.5,46.2,11,${window.pwaMap3dCore.OPENING_CAMERA.pitch},10`);
    expect(url.searchParams.get('layers')).toBe('tours');
  });

  it('keeps a tilted camera and reads the stored switches', async () => {
    window.localStorage.setItem('snowdesk.map.overlay.terrain_filter', 'true');
    window.localStorage.setItem('snowdesk.map.overlay.ski_routes', 'false');
    window.localStorage.setItem('snowdesk.map.overlay.rest_zones', 'true');
    const url = await boot(45);
    expect(url.searchParams.get('at')).toBe('7.5,46.2,11,45,10');
    expect(url.searchParams.get('layers')).toBe('steep,wildlife');
  });
});

describe('map_3d_link prefetch', () => {
  it('fetches the 3x3 elevation block once per view when the pointer arrives', async () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true }));
    await boot(0, fetchSpy);
    fetchSpy.mockClear();
    const link = document.getElementById('map-3d-link');
    link.dispatchEvent(new Event('pointerenter'));
    link.dispatchEvent(new Event('focus'));
    expect(fetchSpy).toHaveBeenCalledTimes(9);
    expect(fetchSpy.mock.calls[0][0]).toMatch(/^https:\/\/t\.example\/9\/\d+\/\d+\.webp$/);
  });
});
