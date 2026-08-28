/*
 * static/js/account_observations.js — the account page's reports list
 * (SNOW-752).
 *
 * /account/observations/ renders observations/partials/_observation_list.html
 * server-side, and its rows are the shared UGC row
 * (includes/_ugc_panel_row.html) carrying one control: a trash. There is no
 * pencil — a field observation has no name to change — and no chevron: it
 * has no detail page. So this page needed no module at all until the trash
 * turned out to be telling half a truth.
 *
 * ONE BEHAVIOUR. Deleting still needs nothing from here: the trash is a
 * plain HTMX form, and observation_delete's empty 200 is what the row's own
 * ``hx-target``/``hx-swap="outerHTML"`` turns into a removal. But that swap
 * empties one ``<li>`` and nothing else, so a page which has just lost its
 * last report keeps rendering as a list of none — observations:list's empty
 * state is a server-side ``{% if not observations %}`` clause, and only a
 * fresh response can carry it.
 *
 * The refetch is the list wrapper's own ``hx-get`` (see
 * observations/my_observations.html), so the URL is written once, in the
 * template. All this module does is say that a report was removed, on
 * ``document``, under the same name the map's community-reports layer
 * listens for: "your reports changed" is one fact, and each surface decides
 * what it costs. On this page it costs a re-read; on the map it costs a
 * refetch of the layer.
 *
 * Recognising the removal is window.pwaRowRemoved's — shared with the map's
 * three panels and the other two account pages, and the reason it is a
 * module rather than a listener here is written up in
 * static/js/row_removed.js (an outerHTML swap detaches the element that made
 * the request before htmx:afterRequest fires, so it takes two events and a
 * mark that rides the xhr).
 *
 * WHY THIS IS A SIBLING OF account_routes.js AND NOT A SHARED MODULE. The
 * genuinely shared halves are already extracted — row_removed.js recognises
 * the removal, the template owns the refetch. What is left in each account
 * module is a selector and an event name, and the other two carry a rename
 * this page has no use for. Merging them would mean a shared module where
 * most of the code runs for one caller, which is the project's "no
 * abstractions until two callers need them" rule read the way round it is
 * meant. account_routes.js's own header makes the same argument.
 *
 * ONLINE-ONLY, like every other surface's delete, and it writes no
 * user-facing strings: a failed request never reaches the handler below, so
 * there is nothing here to report. (Unlike /account/routes/ and
 * /account/favourites/ this page carries no
 * accounts/partials/_htmx_error_banner.html — it has no rename, so it had
 * no write that could fail silently. A failed Remove here is still
 * unannounced, which predates this module and is a gap of its own rather
 * than one to fix from JS: that copy belongs in a template, where
 * makemessages can see it (docs/i18n.md).)
 */

(function accountObservationsInit() {
  'use strict';

  var LIST_SELECTOR = '[data-observation-list]';

  window.pwaRowRemoved?.watch(LIST_SELECTOR, function () {
    document.dispatchEvent(new CustomEvent('snowdesk:reports-changed'));
  });
})();
