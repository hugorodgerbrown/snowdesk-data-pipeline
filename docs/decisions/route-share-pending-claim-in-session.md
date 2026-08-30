---
name: route-share-pending-claim-in-session
description: An unclaimed RouteShare token lives in request.session, which is why route_list and routes_geojson answer an anonymous request
status: current
last-reviewed: 2026-08-30
---

# A pending route-share claim lives in the session (SNOW-764)

**Decision.** Following `/routes/s/<token>/` writes the token into
`request.session["route_shares"]` and redirects to `/?route_share=<token>`.
It does **not** claim anything. `apps.routes.views.route_list` and
`routes_geojson` then answer for a request holding pending tokens *whether
or not it is authenticated*, rendering those shares alongside (above) the
requester's own routes. Claiming — which copies the route onto an account —
needs authentication and is a separate POST.

**Why.** The recipient of a share link is very often signed out when they
tap it, and the link has to survive that. Three properties were needed:

* **It must survive the sign-in round trip.** The token arrives in the URL,
  the visitor signs in, and comes back to a page whose address bar no
  longer names the share. A session carries data across `login()` (Django
  cycles the key, not the contents), needs no schema, and expires by
  itself.
* **It must be visible before it is claimed.** A link that answered "sign
  in to see what you were sent" would ask someone to create an account for
  a thing they have not been shown. So the two surfaces that draw a route —
  the panel list and the map layer — widen; the surface that *writes* does
  not.
* **It must not become an ownership hole.** The widening is keyed on the
  session, never on a URL parameter, so nothing a caller can type reaches
  the pending branch: the token had to pass through the rate-limited
  redirect first.

The alternatives each fail one of those. Claiming on follow writes to an
account the visitor may not have, and silently adds a row to the account of
anyone who taps a link. A `?token=` parameter read directly by the list
endpoints makes both of them token-guessing oracles and does not survive the
round trip. A `PendingClaim` table needs a principal to hang off, which is
exactly what a signed-out recipient has not got.

**Consequences.**

* **Two endpoints are wider than owner-scoped**, and the rule that made them
  safe to reason about ("`Route.objects.for_user()` everywhere") no longer
  holds by inspection. Both carry the widening in their docstrings, and both
  short-circuit on an empty session list before any query, so a visitor who
  never followed a share link pays nothing — which is what keeps the
  homepage's query count where it was.
* **A pending feature carries the share token and no `uuid`.** The rename
  and delete endpoints are addressed by uuid and owner-scoped; handing a
  non-owner one would be giving out an identifier for somebody else's row.
  `static/js/map.js` keys the pending line, its popup and its elevation
  profile off `token` for that reason.
* **The pending list is per-browser, not per-account.** Two devices signed
  into one account do not share it. That is the correct scope — following a
  link is something a browser did — but it means a link opened on a phone
  and then signed into on a laptop has to be opened again there.
* **The list is bounded** by `settings.ROUTE_SHARE_MAX_PENDING` (5), because
  the session is cookie-backed and an unbounded list grows with every link
  followed. The oldest is dropped, not the newest refused: the link just
  followed is the one the visitor means.
* **Dead tokens are pruned on read.** `pending_shares()` drops any token
  whose share has expired or whose route was deleted, so a stale entry stops
  costing a query rather than sitting in the cookie until the session ends.
* **`routes_eligible` no longer means "authenticated"** in
  `apps.public.views._routes_context`. It means "there is a routes list for
  this request"; `routes_upload_eligible` is the authentication question
  that value used to answer, and `static/js/routes.js` reads both.
