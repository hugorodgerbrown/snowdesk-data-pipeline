/*
 * tests/js/test_share.js — window.pwaShare (SNOW-764).
 *
 * static/js/share.js is the helper the bulletin page's share button and the
 * route row's Share control both use. The behaviour worth pinning is
 * `shareOrCopy`'s branching, because it is the part that was written once
 * inline, is easy to get subtly wrong, and whose wrongest version is
 * invisible in manual testing: copying to the clipboard after the user has
 * CANCELLED the share sheet looks like a success and is the opposite of
 * what they asked for.
 *
 * Four outcomes, one per branch:
 *   'shared'    the platform took it.
 *   'cancelled' the user dismissed the sheet (AbortError) — and the
 *               clipboard must NOT have been written.
 *   'copied'    no share API, or it rejected for any other reason.
 *   'failed'    neither worked.
 *
 * jsdom exposes neither navigator.share nor navigator.clipboard, so both
 * are installed per test with vi.stubGlobal — which is also why the module
 * reads them off `window.navigator` at call time rather than capturing them
 * at parse time.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/share.js';

const URL_UNDER_TEST = 'https://snowdesk.app/routes/s/abc123/';

/** Install a navigator with the given share/clipboard behaviour.
 *
 * @param {object} parts Partial navigator — `share` and/or `clipboard`.
 * @returns {object} The installed navigator stand-in.
 */
function stubNavigator(parts) {
  const nav = Object.assign({}, parts);
  vi.stubGlobal('navigator', nav);
  // `window.navigator` is what the module reads; in jsdom `window` and the
  // global object are the same, but say so rather than assume it.
  window.navigator = nav;
  return nav;
}

beforeEach(() => {
  document.title = 'A bulletin page';
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('shareOrCopy', () => {
  it('hands the payload to the native sheet when there is one', async () => {
    const share = vi.fn(() => Promise.resolve());
    stubNavigator({ share: share });

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(outcome).toBe('shared');
    expect(share).toHaveBeenCalledWith({
      url: URL_UNDER_TEST,
      title: 'A bulletin page',
    });
  });

  it('uses a supplied title over the document one', async () => {
    const share = vi.fn(() => Promise.resolve());
    stubNavigator({ share: share });

    await window.pwaShare.shareOrCopy(URL_UNDER_TEST, 'Col de Balme');

    expect(share.mock.calls[0][0].title).toBe('Col de Balme');
  });

  it('does NOT copy when the user cancels the sheet', async () => {
    // The bug this test exists for: an AbortError means they changed their
    // mind, and falling back to the clipboard does the thing they declined.
    const abort = new Error('cancelled');
    abort.name = 'AbortError';
    const writeText = vi.fn(() => Promise.resolve());
    stubNavigator({
      share: vi.fn(() => Promise.reject(abort)),
      clipboard: { writeText: writeText },
    });

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(outcome).toBe('cancelled');
    expect(writeText).not.toHaveBeenCalled();
  });

  it('falls back to the clipboard on any other rejection', async () => {
    // Chrome on desktop exposes navigator.share and may reject when the
    // platform cannot actually show a sheet. Without this branch the share
    // silently no-ops.
    const writeText = vi.fn(() => Promise.resolve());
    stubNavigator({
      share: vi.fn(() => Promise.reject(new Error('NotAllowedError'))),
      clipboard: { writeText: writeText },
    });

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(outcome).toBe('copied');
    expect(writeText).toHaveBeenCalledWith(URL_UNDER_TEST);
  });

  it('copies when there is no share API at all', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    stubNavigator({ clipboard: { writeText: writeText } });

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(outcome).toBe('copied');
    expect(writeText).toHaveBeenCalledWith(URL_UNDER_TEST);
  });

  it('does not ask a platform that says it cannot take the payload', async () => {
    const share = vi.fn(() => Promise.resolve());
    const writeText = vi.fn(() => Promise.resolve());
    stubNavigator({
      share: share,
      canShare: vi.fn(() => false),
      clipboard: { writeText: writeText },
    });

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(share).not.toHaveBeenCalled();
    expect(outcome).toBe('copied');
  });

  it('reports failure when neither route works', async () => {
    stubNavigator({});

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(outcome).toBe('failed');
  });

  it('reports failure when the clipboard write is refused', async () => {
    stubNavigator({
      clipboard: { writeText: vi.fn(() => Promise.reject(new Error('denied'))) },
    });

    const outcome = await window.pwaShare.shareOrCopy(URL_UNDER_TEST);

    expect(outcome).toBe('failed');
  });
});

describe('createShare', () => {
  it('resolves with the url the endpoint answers', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ url: URL_UNDER_TEST }),
      }),
    );

    const url = await window.pwaShare.createShare('/routes/x/share/', 'tok');

    expect(url).toBe(URL_UNDER_TEST);
    expect(globalThis.fetch.mock.calls[0][1].headers['X-CSRFToken']).toBe('tok');
  });

  it('rejects on a non-2xx rather than resolving with nothing', async () => {
    // A null flowing on into shareOrCopy would open a share sheet for the
    // string "null".
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 404 }));

    await expect(
      window.pwaShare.createShare('/routes/x/share/', 'tok'),
    ).rejects.toBeTruthy();
  });

  it('rejects on a 200 carrying no url', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    );

    await expect(
      window.pwaShare.createShare('/routes/x/share/', 'tok'),
    ).rejects.toBeTruthy();
  });
});

describe('claim', () => {
  it('posts with the HTMX header the endpoint requires', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, text: () => Promise.resolve('<li>row</li>') }),
    );

    const html = await window.pwaShare.claim('/routes/partials/share/t/claim/', 'tok');

    expect(html).toBe('<li>row</li>');
    const init = globalThis.fetch.mock.calls[0][1];
    expect(init.method).toBe('POST');
    expect(init.headers['HX-Request']).toBe('true');
  });

  it('rejects with the response so the caller can read its status', async () => {
    // 409 at the route cap needs a different line from a 404 on an expired
    // link, so the status has to survive the rejection.
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 409 }));

    await expect(
      window.pwaShare.claim('/routes/partials/share/t/claim/', 'tok'),
    ).rejects.toMatchObject({ status: 409 });
  });
});
