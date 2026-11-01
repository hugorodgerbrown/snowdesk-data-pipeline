/*
 * static/js/signin_cta.js — the one sign-in prompt the map sheets show.
 *
 * SNOW-672. Three surfaces gate on being signed in — favourites, routes and
 * community reports — and all three showed the same thing: a line of prompt
 * text and a full-width pill link to the sign-in page. Each had built it
 * itself, which meant one class string written out three times, in two
 * different idioms (favourites.js and routes.js as DOM nodes, report.js as an
 * innerHTML string). A class string assembled in JavaScript reaches the page
 * exactly as a template one does — which is why bin/ds-lint reads static/js —
 * so three copies is three places for the treatment to drift.
 *
 * Callers pass their own already-translated strings. This module holds no
 * user-facing text of its own: the surfaces read theirs from the strings
 * <template> via window.pwaStrings, and hand the values in.
 *
 * SNOW-903: and every sign-in link the map hands out returns the visitor to
 * where they were when they TAPPED it. `withNext(url)` appends
 * `next=<location.pathname + location.search>` — a same-site relative path,
 * which the sign-in view's `safe_next` passes unchanged; never `location.href`
 * (absolute) and never the hash — unless the url already names a `next`, in
 * which case it is a deliberate destination and is left alone. `bindNext(a)`
 * sets an anchor's href that way and recomputes it on every click, from the
 * url the anchor first carried, because the map rewrites its own URL
 * (`?d=`, `?panel=`) through `history.replaceState` while a sheet stays open.
 * `build()` binds its pill the same way. The place sheet's signed-out star
 * must use `bindNext` rather than the server's `{% sign_in_href %}`: the
 * sheet is an HTMX fragment, and the request path the server sees is the
 * fragment's, not the map's.
 *
 * Load order is not a constraint here. Every caller reaches
 * window.snowdeskSigninCta from inside a render function that runs on user
 * interaction, long after every deferred script on the page has executed.
 */

(function () {
  'use strict';

  // The pill itself. Tokens only — bg-status-info-* is the informational
  // status pair from @theme, not a raw palette colour.
  const LINK_CLASS =
    'block w-full rounded-pill bg-status-info-bg text-status-info-text ' +
    'text-sm font-medium text-center py-2 px-4';

  const PROMPT_CLASS = 'text-sm text-text-2 mb-3';

  // SNOW-903: where an anchor bound by `bindNext` keeps the url it first
  // carried, so each recompute starts from the bare sign-in url rather than
  // from an href that already names the previous `next`.
  const ORIGINAL_HREF_ATTR = 'data-signin-href';

  /**
   * The root-relative form of a same-origin sign-in url with a `next`
   * naming the page as it is now, or null when there is nothing to rewrite
   * (another origin or scheme — a `//host` included — an existing `next`,
   * or a url that cannot be parsed).
   *
   * The result is always assembled behind a literal `/`, from the parsed
   * path with its leading slashes folded, so whatever an href read back
   * from the DOM held, what is written is a path on this site.
   *
   * @param {string} url The sign-in url, relative or absolute.
   * @returns {string|null}
   */
  function rootRelativeWithNext(url) {
    if (!url) return null;
    let parsed;
    try {
      parsed = new URL(url, window.location.origin);
    } catch (_err) {
      return null;
    }
    // Only this site's own sign-in page is given a `next`, so the page url
    // is never sent anywhere else.
    if (parsed.origin !== window.location.origin) return null;
    if (parsed.searchParams.has('next')) return null;
    parsed.searchParams.set('next', window.location.pathname + window.location.search);
    return '/' + parsed.pathname.replace(/^\/+/, '') + parsed.search + parsed.hash;
  }

  /**
   * Give a sign-in url a `next` naming the page as it is now.
   *
   * @param {string} url The sign-in url, relative or absolute.
   * @returns {string} The url as a root-relative path with
   *   `next=<path + query>`, or the url unchanged when it already carries a
   *   `next`, points at another origin or scheme, or cannot be parsed.
   */
  function withNext(url) {
    return rootRelativeWithNext(url) ?? url;
  }

  /**
   * Point a sign-in anchor at `withNext()` of its own href, now and on every
   * click, so the `next` is the live page url at the moment it is tapped.
   * Binding the same anchor twice adds no second listener. An href that is
   * not this site's is left exactly as it was.
   *
   * @param {HTMLAnchorElement} anchor
   * @returns {HTMLAnchorElement}
   */
  function bindNext(anchor) {
    if (!anchor) return anchor;
    const bound = anchor.hasAttribute(ORIGINAL_HREF_ATTR);
    if (!bound) anchor.setAttribute(ORIGINAL_HREF_ATTR, anchor.getAttribute('href') || '');
    const refresh = () => {
      const next = rootRelativeWithNext(anchor.getAttribute(ORIGINAL_HREF_ATTR) || '');
      if (next) anchor.setAttribute('href', next);
    };
    refresh();
    // Capture phase, so the href is current before any other click handler
    // (or the browser's own navigation) reads it.
    if (!bound) anchor.addEventListener('click', refresh, true);
    return anchor;
  }

  /**
   * Build the gate as DOM nodes.
   *
   * Nodes rather than an innerHTML string so a URL or a translated string
   * can never be parsed as markup — the DOM-not-markup discipline the
   * calling modules already use for anything URL-bearing.
   *
   * @param {string|null} signinUrl Sign-in URL, or null/'' to render the
   *   prompt text alone (the caller has nowhere to send the user).
   * @param {string} promptText Translated sentence explaining the gate.
   * @param {string} ctaText Translated label for the pill link.
   * @returns {HTMLDivElement}
   */
  function build(signinUrl, promptText, ctaText) {
    const wrap = document.createElement('div');
    const p = document.createElement('p');
    p.className = PROMPT_CLASS;
    p.textContent = promptText;
    wrap.appendChild(p);
    if (signinUrl) {
      const a = document.createElement('a');
      a.setAttribute('href', signinUrl);
      a.className = LINK_CLASS;
      a.textContent = ctaText;
      // SNOW-903: back to where the visitor was when they tapped it.
      bindNext(a);
      wrap.appendChild(a);
    }
    return wrap;
  }

  window.snowdeskSigninCta = {
    LINK_CLASS: LINK_CLASS,
    PROMPT_CLASS: PROMPT_CLASS,
    build: build,
    withNext: withNext,
    bindNext: bindNext,
  };
})();
