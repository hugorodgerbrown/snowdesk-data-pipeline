/*
 * tests/js/test_signin_cta.js — the shared sign-in gate (SNOW-672).
 *
 * Three map surfaces gate on being signed in, and each used to build its own
 * copy of the same prompt-plus-pill. The copies were identical when they were
 * written; nothing kept them that way. These tests assert the two properties
 * that made consolidating them worth doing:
 *
 *   1. The DOM builder and the class strings report the same treatment, so
 *      report.js's markup string cannot drift from the nodes favourites.js
 *      and routes.js build.
 *   2. A missing sign-in URL still renders the prompt. That branch is real —
 *      the surfaces render the gate whenever the visitor is ineligible, and
 *      a template that has nowhere to send them must not produce a link to
 *      nowhere.
 *
 * The module is imported by tests/js/setup.js, matching the page, where it
 * loads ahead of the three surfaces that call it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

const PROMPT = 'Sign in to save favourites.';
const CTA = 'Sign in';
const URL = '/accounts/sign-in/';

describe('window.snowdeskSigninCta', () => {
  it('builds a prompt and a pill link to the sign-in URL', () => {
    const el = window.snowdeskSigninCta.build(URL, PROMPT, CTA);

    const p = el.querySelector('p');
    expect(p.textContent).toBe(PROMPT);

    const a = el.querySelector('a');
    // SNOW-903: with a `next` naming the page it was built on.
    const here = window.location.pathname + window.location.search;
    expect(a.getAttribute('href')).toBe(`${URL}?next=${encodeURIComponent(here)}`);
    expect(a.textContent).toBe(CTA);
  });

  it('renders the prompt alone when there is no sign-in URL', () => {
    for (const missing of [null, '']) {
      const el = window.snowdeskSigninCta.build(missing, PROMPT, CTA);
      expect(el.querySelector('p').textContent).toBe(PROMPT);
      expect(el.querySelector('a')).toBeNull();
    }
  });

  it('gives the built nodes the same classes it publishes to markup callers', () => {
    // report.js assigns its gate with innerHTML, so it interpolates these two
    // strings instead of using build(). If the constants and the nodes ever
    // disagree, that surface silently renders a different pill.
    const el = window.snowdeskSigninCta.build(URL, PROMPT, CTA);
    expect(el.querySelector('p').className).toBe(window.snowdeskSigninCta.PROMPT_CLASS);
    expect(el.querySelector('a').className).toBe(window.snowdeskSigninCta.LINK_CLASS);
  });

  it('escapes nothing itself — the text is set as text, not markup', () => {
    // The DOM-not-markup discipline the calling modules rely on: a translated
    // string containing a tag must land as characters, not as an element.
    const el = window.snowdeskSigninCta.build(URL, '<b>bold</b>', CTA);
    expect(el.querySelector('p').querySelector('b')).toBeNull();
    expect(el.querySelector('p').textContent).toBe('<b>bold</b>');
  });
});

describe('withNext and bindNext (SNOW-903)', () => {
  const cta = () => window.snowdeskSigninCta;
  const nextOf = (href) => new globalThis.URL(href, window.location.origin).searchParams.get('next');

  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('appends the path and query, never the origin or the hash', () => {
    window.history.replaceState(null, '', '/map/?d=2026-01-06&panel=routes#frag');

    const href = cta().withNext(URL);

    expect(href.startsWith(URL)).toBe(true);
    expect(nextOf(href)).toBe('/map/?d=2026-01-06&panel=routes');
  });

  it('encodes the query so its & and ? stay inside the next value', () => {
    window.history.replaceState(null, '', '/map/?d=2026-01-06&panel=routes');

    const href = cta().withNext(URL);

    expect(href).toBe(`${URL}?next=%2Fmap%2F%3Fd%3D2026-01-06%26panel%3Droutes`);
  });

  it('keeps a url that already names its destination', () => {
    const fixed = `${URL}?next=%2Froutes%2Fexample%2F`;
    window.history.replaceState(null, '', '/map/?d=2026-01-06');

    expect(cta().withNext(fixed)).toBe(fixed);
  });

  it('keeps an absolute url absolute', () => {
    window.history.replaceState(null, '', '/map/');
    const absolute = `${window.location.origin}${URL}`;

    expect(cta().withNext(absolute)).toBe(`${absolute}?next=%2Fmap%2F`);
  });

  it('recomputes a bound anchor when it is tapped after the url moved', () => {
    window.history.replaceState(null, '', '/map/?d=2026-01-06');
    const a = document.createElement('a');
    a.setAttribute('href', URL);
    a.addEventListener('click', (event) => event.preventDefault());
    document.body.appendChild(a);
    cta().bindNext(a);
    expect(nextOf(a.getAttribute('href'))).toBe('/map/?d=2026-01-06');

    window.history.replaceState(null, '', '/map/?d=2026-01-07&panel=favourites');
    a.click();

    expect(nextOf(a.getAttribute('href'))).toBe('/map/?d=2026-01-07&panel=favourites');
    a.remove();
  });

  it('binds an anchor once, however often it is bound', () => {
    const a = document.createElement('a');
    a.setAttribute('href', URL);
    const spy = vi.spyOn(a, 'addEventListener');

    cta().bindNext(a);
    cta().bindNext(a);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(a.getAttribute('href').match(/next=/g)).toHaveLength(1);
  });
});
