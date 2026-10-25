---
name: the-example-route-is-a-flagged-share
description: Example route (SNOW-1230): one RouteShare flagged is_example, via /routes/example/, never claimable; its walkthrough never locks the map
status: current
last-reviewed: 2026-10-25
---

# The example route is a flagged share

## Context

SNOW-1230 replaced the welcome card's "Take the one-minute tour" with a
task: "Explore Snowdesk" offers *Find a resort*, *Explore an example route*
or *Explore the map*. Route analysis (the slope under the line, the point
card, the bulletin for the day) is the thing Snowdesk does that nothing else
does, and a first-time visitor has no GPX to hand. So they are given one:
a real tour (Mont Fort – Col de la Chaux, from the canonical corpus) on a
past day with a full bulletin (10 April 2026), so the demonstration is
complete even in October.

## Decision

**The example is an ordinary `RouteShare` with two extra columns —
`is_example` and `example_date` — and is opened exactly as a followed share
is.** `/routes/example/` (`route_example`) looks the share up, puts its token
in the session's pending list as `route_share_redirect` does, and redirects
to `/map/?route_share=<token>&example=1&d=<example_date>`. From there the
panel, the profile, the point card and the terrain data read it as they read
any pending share.

What the flags change, and nothing else:

- **It never expires.** `RouteShareQuerySet.active()` and `is_live` treat the
  example as live whatever its `expires_at`. Deleting its route still stops it.
- **It is never claimable.** `claimable()` is `active()` less the example,
  and `claim_route_share` reads `claimable()`. Its panel row reads
  "Example route ·" and carries no Save; the map popup carries a note naming
  the day instead of a claim button.
- **There is at most one.** A partial unique constraint
  (`routes_routeshare_one_example`) holds it, and `set_example_route` is the
  only writer.

The route belongs to `example-routes@snowdesk.invalid`, an account with an
unusable password, so the tour is in nobody's own list.

Two widenings came with it, both Hugo's calls on the ticket:

- **Terrain data for any shared route.** `routes/s/<token>/terrain.json`
  answers for a token *this session holds*, signed in or not — gated on the
  session exactly as the pending branches of `route_list` and
  `routes_geojson` are (see
  [route-share-pending-claim-in-session](route-share-pending-claim-in-session.md)).
- **The point card for everyone.** It reads those rows, so it now opens on a
  shared route as on an owned one.

And one new endpoint: `routes/s/<token>/dismiss/` drops a token from the
session. The walkthrough calls it on Exit and Finish, so the example stops
being drawn and listed until it is opened again.

### The walkthrough never locks the map

Asked whether the tour should be locked down when a visitor "goes rogue",
Hugo chose not to. `map_example_guide.js` is not a map overlay and does not
register with `pwaMapOverlays`; every control keeps working. Going off-script
folds the guide into a paused pill that keeps its step:

- the example's route is not the one open (closed, or another opened) →
  *Resume* reopens it;
- the map is on another day → *Back to 10 Apr*, and the "Example day" badge
  comes off the date row, because the badge never sits on a day the example
  did not choose.

Leaving — Exit or Finish — always closes the route, drops it from the
session, scrubs back to today and says so in a toast ("Back to today ·
25 Oct"). The date jump in is announced first (step one is the only step
with nothing to do but read), and the date jump out is announced last.

## Alternatives considered

- **A model of its own (`ExampleRoute`).** Every surface that reads a route
  share would have needed a second branch. The flag reuses them all.
- **Copy the example onto the visitor's account.** Writes a tour they never
  skied into their list, and needs an account. Rejected; a share needs
  neither.
- **A static fixture rendered client-side.** Would skip the server's terrain
  and leg pipeline, so it would show something no upload produces.
- **Lock the map during the walkthrough.** Rejected by Hugo: the map is the
  lesson, and a locked map teaches nothing about using it.

## Consequences

- Each environment needs one `set_example_route --commit` after deploy.
  Until then the welcome card leaves the example option out
  (`example_route_url` is empty) and the routes panel's empty state does
  not link it.
- Changing the example tour or day is a command run, not a deploy. A link
  already handed out to `/routes/example/` follows the change.
