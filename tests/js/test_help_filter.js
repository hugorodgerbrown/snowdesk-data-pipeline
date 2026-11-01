/*
 * tests/js/test_help_filter.js — Vitest unit tests for
 * static/js/help_filter.js (SNOW-743).
 *
 * The filter hides /help/ topics that do not match, opens and marks the
 * ones that do, hides a heading left empty, widens a reader's word with a
 * short synonym map, never reads an illustration's sample copy, and puts
 * the page back exactly as it was when the box is cleared.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

function buildFixture({ withStrings = true } = {}) {
  document.body.innerHTML = `
    <main>
      <div data-help-filter hidden>
        <input data-help-filter-input>
        <p data-help-filter-status></p>
      </div>
      ${withStrings ? `<template id="help-filter-strings-template">
        <span data-string="no-match">Rien ne correspond.</span>
        <span data-string="one-match">Un sujet.</span>
        <span data-string="some-match">%(count)s sujets.</span>
      </template>` : ''}
      <h2 data-hook="help-section" id="s-start">Getting started</h2>
      <details class="snowdesk-panel" id="help-topic-overview">
        <summary>What is Snowdesk</summary>
        <p>Avalanche bulletins on one <strong>map</strong>.</p>
      </details>
      <h2 data-hook="help-section" id="s-map">The map</h2>
      <details class="snowdesk-panel" id="help-topic-routes">
        <summary>Routes</summary>
        <p>Upload a route and see its slope.</p>
      </details>
      <details class="snowdesk-panel" id="help-topic-downloads" open>
        <summary>Offline downloads</summary>
        <div data-testid="help-topic-downloads-illustration"><p>Verbier telepherique</p></div>
        <p>Save an area to use the map offline.</p>
      </details>
      <details class="snowdesk-panel" id="help-topic-lifts">
        <summary>Lifts</summary>
        <p>Every téléphérique track is drawn.</p>
      </details>
    </main>
  `;
}

async function load() {
  vi.resetModules();
  await import('../../static/js/i18n_strings.js');
  await import('../../static/js/help_filter.js');
}

const $ = (id) => document.getElementById(id);
const input = () => document.querySelector('[data-help-filter-input]');
const status = () => document.querySelector('[data-help-filter-status]').textContent;
const type = (value) => {
  input().value = value;
  input().dispatchEvent(new Event('input'));
};

beforeEach(async () => {
  buildFixture();
  await load();
});

describe('help filter', () => {
  it('reveals the box only once the script runs', () => {
    expect(document.querySelector('[data-help-filter]').hidden).toBe(false);
  });

  it('leaves a matching topic open and visible and hides the rest', () => {
    type('upload');

    expect($('help-topic-routes').hidden).toBe(false);
    expect($('help-topic-routes').open).toBe(true);
    expect($('help-topic-overview').hidden).toBe(true);
    expect($('help-topic-downloads').hidden).toBe(true);
  });

  it('hides a heading whose section has no surviving topic', () => {
    type('upload');

    expect($('s-start').hidden).toBe(true);
    expect($('s-map').hidden).toBe(false);
  });

  it('finds a topic through a synonym its text does not use', () => {
    type('gpx');

    expect($('help-topic-routes').hidden).toBe(false);
    expect($('help-topic-routes').querySelector('mark').textContent.toLowerCase()).toBe('route');
  });

  it('searches a synonym key literally first when the page uses the word', () => {
    // "track" is a synonym key for "route", and the Lifts topic says
    // "track": the literal hit wins, so Routes is not widened in.
    type('track');

    expect($('help-topic-lifts').hidden).toBe(false);
    expect($('help-topic-routes').hidden).toBe(true);
    expect(document.querySelectorAll('mark')).toHaveLength(1);
    expect(status()).toBe('Un sujet.');
  });

  it('ignores case and accents', () => {
    type('TELEPHERIQUE');

    expect($('help-topic-lifts').hidden).toBe(false);
    expect($('help-topic-lifts').querySelector('mark').textContent).toBe('téléphérique');
  });

  it('never matches or marks an illustration', () => {
    type('verbier');

    expect($('help-topic-downloads').hidden).toBe(true);
    expect(document.querySelector('mark')).toBeNull();
    expect(status()).toBe('Rien ne correspond.');
  });

  it('wraps the matched run in a mark without touching the markup around it', () => {
    type('map');

    const overview = $('help-topic-overview');
    expect(overview.querySelector('strong > mark').textContent).toBe('map');
    expect(overview.querySelector('p').textContent).toBe('Avalanche bulletins on one map.');
    const downloads = $('help-topic-downloads').querySelectorAll('mark');
    expect(downloads).toHaveLength(1);
  });

  it('puts the page back as it was when cleared', () => {
    type('upload');
    type('');

    ['help-topic-overview', 'help-topic-routes', 'help-topic-downloads', 'help-topic-lifts']
      .forEach((id) => expect($(id).hidden).toBe(false));
    expect($('s-start').hidden).toBe(false);
    expect($('help-topic-downloads').open).toBe(true);
    expect($('help-topic-routes').open).toBe(false);
    expect(document.querySelector('mark')).toBeNull();
    expect($('help-topic-overview').querySelector('p').childNodes).toHaveLength(3);
    expect(status()).toBe('');
  });

  it('counts the matches in the strings the page renders', () => {
    type('route');
    expect(status()).toBe('Un sujet.');

    type('map');
    expect(status()).toBe('2 sujets.');
  });

  it('falls back to English when the strings are missing', async () => {
    buildFixture({ withStrings: false });
    await load();

    type('zzz');
    expect(status()).toBe('No topic matches that. Try a different word.');
    type('map');
    expect(status()).toBe('2 topics match.');
  });
});
