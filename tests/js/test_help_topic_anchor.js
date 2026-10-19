/*
 * tests/js/test_help_topic_anchor.js — Vitest unit tests for
 * static/js/help_topic_anchor.js (SNOW-1119).
 *
 * The map's help mode links each control to its /help/ topic, and every
 * topic is a collapsed <details>. A link that lands on a closed panel
 * shows a title with the answer folded away, so the page opens the one
 * the address names — on arrival and when the hash changes.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function buildFixture() {
  document.body.innerHTML = `
    <details id="help-topic-layers"><summary>Layers</summary><p>…</p></details>
    <div id="help-topic-slope"><details><summary>Slope</summary></details></div>
    <details id="help-topic-routes"><summary>Routes</summary></details>
  `;
  Element.prototype.scrollIntoView = vi.fn();
}

async function load(hash) {
  window.history.replaceState(null, '', hash || '/help/');
  vi.resetModules();
  await import('../../static/js/help_topic_anchor.js');
}

const isOpen = (id) => {
  const el = document.getElementById(id);
  return (el instanceof HTMLDetailsElement ? el : el.querySelector('details')).open;
};

beforeEach(buildFixture);
afterEach(() => window.history.replaceState(null, '', '/'));

describe('help topic anchors', () => {
  it('opens the topic the link lands on, and scrolls to it', async () => {
    await load('/help/#help-topic-layers');

    expect(isOpen('help-topic-layers')).toBe(true);
    expect(isOpen('help-topic-routes')).toBe(false);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it('opens a panel wrapped in an anchor', async () => {
    await load('/help/#help-topic-slope');

    expect(isOpen('help-topic-slope')).toBe(true);
  });

  it('opens a topic named after the page has loaded', async () => {
    await load('/help/');

    window.history.replaceState(null, '', '/help/#help-topic-routes');
    window.dispatchEvent(new HashChangeEvent('hashchange'));

    expect(isOpen('help-topic-routes')).toBe(true);
  });

  it('leaves every topic closed with no hash, or an unknown one', async () => {
    await load('/help/#no-such-topic');

    expect(isOpen('help-topic-layers')).toBe(false);
    expect(isOpen('help-topic-routes')).toBe(false);
  });
});
