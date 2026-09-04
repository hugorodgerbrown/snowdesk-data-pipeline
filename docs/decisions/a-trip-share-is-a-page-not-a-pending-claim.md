---
name: a-trip-share-is-a-page-not-a-pending-claim
description: A trip link opens a public page; it needs none of SNOW-764's session pending-claim machinery, because a trip has somewhere to be seen
status: current
last-reviewed: 2026-09-04
---

# A trip share is a page, not a pending claim

## Decision

**A trip's share link opens `/trips/s/<token>/` — a real, public page — and
that is the whole mechanism.** No token rides in `request.session`, no
endpoint is widened for anonymous callers, and no per-browser pending list
is bounded, retried or pruned.

Concretely, a trip share needs none of the following, all of which SNOW-764
built for a route share:

- `PENDING_SESSION_KEY` and the four functions around it
  (`pending_tokens` / `add_pending_token` / `drop_pending_token` /
  `pending_shares`);
- `ROUTE_SHARE_MAX_PENDING`, the cookie-size bound on that list;
- the prune-as-you-read pass that drops dead tokens from the session;
- the "deliberately wider than owner-scoped" branches on `route_list` and
  `routes_geojson`, and the three properties that keep that widening
  honest;
- `is_top_level_navigation`, the `Sec-Fetch-Dest` guard that stops an
  `<img src>` planting a token in every visitor's session.

## Why

**Because a route share had nowhere to be seen, and a trip does.**

A `Route` is a polyline. It has no page — a route is read on the map, as a
line among the user's own lines — so `route_share_redirect` had to land the
recipient *somewhere*, and the only somewhere was `/`. That is a map of
their own data, which does not include the route they were just sent. Making
the line visible there meant carrying the token past the redirect, which
meant the session; and making the panel and the GeoJSON feed draw it meant
widening two owner-scoped endpoints for an anonymous caller. Each of those
follows from the first fact, and each brought its own bound, its own guard
and its own decision doc
([`route-share-pending-claim-in-session`](route-share-pending-claim-in-session.md)).

A trip has a page. The link opens it, the page states the plan — the day,
the meeting time, the meeting point, the route drawn with a marker, the
figures and the organiser's note — and every control on it is addressed by
the token the visitor already holds. Nothing has to survive a redirect,
because there is no redirect.

**The sign-in round trip stops being the problem it was.** The session
existed largely to carry an intention across sign-in: the link landed on the
map, the visitor signed in, came back, and the token that brought them was
gone from the URL. A trip link IS the URL. Signing in and re-opening the
same link lands on the same page in the same state, so the intention needs
no server-side memory — the browser's history is holding it.

**Two acts, neither of which needs a claim.** A recipient can **join** the
trip (SNOW-822) or **save the route** (SNOW-824), both `POST`s addressed by
the token, both requiring an account. Neither is a *claim* in SNOW-764's
sense — an intention recorded now and acted on later — so neither needs a
place to be recorded.

## Consequences

- **A signed-out recipient sees the trip and a sign-in link.** They can read
  everything before being asked for anything, which is the property the
  pending list was working to preserve, obtained here for free.
- **The cost is one page load per action.** A signed-out visitor who wants
  to join signs in and re-opens the link, rather than having their intention
  replayed for them. Accepted: `apps.accounts.views.sign_in_view` does not
  read a `?next=` parameter today, so the roster's CTA does not pretend to
  pass one. Teaching sign-in about `next` would close this and is its own
  ticket.
- **Saving the route copies the trip's SNAPSHOT**, not the organiser's
  `Route`. It works after they delete theirs, and what the viewer gets is
  the geometry they were shown. Nulls stay null; the copy carries no timing
  and no source filename, because a trip is a plan and was never a
  recording.
- **Saving does not join and joining does not save.** They are different
  acts — one puts a track on your map, the other puts you in a group — and a
  recipient who wants the line without committing to the day can take it.
- **Nothing links the saved copy back to the trip.** It is an ordinary route
  the viewer owns. The "already saved" state is therefore detected by exact
  geometry match rather than by a marker row: a marker table would be exact,
  but it is a new model for a UI nicety, and a false negative costs a
  duplicate the user can delete.
- **The route-copy write is shared.** `claim_route_share` and
  `save_trip_route` both go through
  `apps.routes.services.shares.write_route_copy`, so the cap dance
  (unlocked pre-check, locked re-check, create) exists once for both.
