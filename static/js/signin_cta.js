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
      wrap.appendChild(a);
    }
    return wrap;
  }

  window.snowdeskSigninCta = {
    LINK_CLASS: LINK_CLASS,
    PROMPT_CLASS: PROMPT_CLASS,
    build: build,
  };
})();
