---
name: a-trip-is-one-object-with-a-roster
description: Trip is one row with a TripParticipant roster, never copied per person; the geometry is a snapshot and the meeting point a per-trip Location
status: current
last-reviewed: 2026-09-04
---

# A trip is one object with a roster

## Decision

**A `Trip` is one row, and everybody on it is a `TripParticipant` pointing at
that row.** It is never copied per recipient, the way a `Route` is.

Four rules follow from it, and each is enforced in code:

- **The organiser gets a participant row at creation.** `Trip.created_by`
  names them, but they are *also* on the roster, so "everyone on this trip"
  is one relation — `Trip.objects.for_user()` filters on
  `participants__user` and needs no union.
- **Organiser-ness is derived, never stored twice.**
  `trip.created_by_id == viewer.pk`, computed where it is read. There is no
  `is_organiser` column that could disagree with the FK.
- **The route geometry is a snapshot**, copied onto the trip at creation and
  never re-read through `Trip.route`. That FK is provenance only.
- **The meeting point is a `Location` minted for that trip alone**, with no
  name, no kind, and `on_delete=PROTECT`.

## Why

**Because everyone on a trip is meeting each other.** `RouteShare` copies
because a route is a *file* — handing it to a friend gives them their own,
which they can rename or delete without touching yours, and that is exactly
right. A trip is the opposite: one place, one time, one group. A copy per
person would produce five unrelated rows describing one meeting, with no way
to answer "who is coming", and it would destroy the relation a later groups
feature would be built from at the moment it is created.

**Because the organiser gets on the roster or every query gets a special
case.** The alternative — the organiser is `created_by` and nobody else —
means each roster count, each list and each template loop has to remember to
add one. One of them eventually will not.

**Because a trip must not change under the people who joined it.** The
snapshot is the load-bearing half. If the page read through `Trip.route`,
then renaming a route would rename everybody's trip, editing one would move
the line they agreed to ski, and deleting one would empty a page four people
have in their calendar. Copying seven fields at creation makes a trip a
statement of what was shared, which is what a plan sent to other people has
to be.

`ascent_m` and `descent_m` are copied **as null when they are null**. `Route`
is explicit that null means "the source file carried no elevation data", not
"flat", and flattening one into the other is a safety-relevant lie about
terrain somebody is about to ski.

**Because `PROTECT` is what makes the orphan sweep honest.**
`apps.favourites.services._delete_location_if_orphaned` asks the database
whether anything still references a location, by attempting the delete and
catching `ProtectedError`. That probe is correct only while *every* referent
of `Location` protects it. A `SET_NULL` or `CASCADE` trip would answer "no,
nothing references this" while still pointing at the row — and would break
the sweep for favourites and observations, not just for trips.

The meeting point is minted per trip rather than reused via
`anchor_location()`: two groups meeting at the same lift station are two
rows, and a shared row would mean one organiser's edit moved the other
group's meeting point.

**Because the day and the time are wall-clock.** `date` and `start_time` are
a plain `DateField` and `TimeField`, never combined into an aware datetime.
This looks like a breach of the project's tzinfo convention and is not one —
neither field is a datetime. Converting would be the bug: "07:30" is what
the organiser typed and what everybody standing at the lift station reads
off their own watch, and rendering it through a friend's phone timezone
would show a group member abroad a different time for the same meeting.

## Consequences

- **A trip has one link, not many.** `TripShare` is not a model; the token
  lives on `Trip` (SNOW-821). `RouteShare` is a separate table because a
  route is handed out in many independent grants, each worth auditing; a
  trip has one roster, so it has one link. The cost is stated rather than
  hidden: no per-link audit trail, and no way to hand two groups different
  links. Neither is a thing a trip needs — the roster is the record of who
  came. Sharing a trip that already has a link **rotates** it, which is the
  organiser's whole revoke-and-reshare and the reason a link sent to the
  wrong person is recoverable.
- **The link's window is measured from the trip's DATE**, not from when it
  was minted (`share_expiry_for`). `ROUTE_SHARE_MAX_AGE_DAYS` measures from
  the mint because a route has no date of its own; a trip does, and a
  mint-time window would kill the link of a trip planned three months out
  two months before the day it exists for.
- **The share page is `noindex`, and robots.txt is left alone.**
  `Disallow: /trips/` would prefix-match `/trips/s/<token>/` and block the
  unfurlers a share link exists to serve. `noindex` is the correct
  instrument for an unguessable URL, and it lives in
  `includes/_page_meta.html` beside `sharing` because that partial is the
  one emitter for head metadata.
- **The organiser cannot leave their own trip**, and cannot remove anybody
  else (SNOW-822). A trip whose organiser is not on it is incoherent —
  nobody is answerable for the plan, and the roster no longer holds the
  person whose name answers "who sent me this" — so a leave from them is
  409 with a message pointing at Delete. Removal is a moderation surface
  with its own questions (who is told, what they see, whether they can
  rejoin) for an object that lasts one day, so there is no service function
  for it at all. Deleting the trip is the one exit, and it removes it for
  everyone — which the delete confirmation says out loud, naming the number
  of other people.
- **Anyone holding the link sees the COUNT; only participants see the
  NAMES; the organiser is named to everyone** (`roster_names_visible_to`).
  A trip link travels — forwarded out of a group chat, pasted into another
  one — and the people on the roster did not agree to be listed to whoever
  it reached, so joining is what earns the group's names. The organiser is
  the exception because their name is already the answer to "who is this
  from", and withholding it would make the page more anonymous than the
  message that carried it. A person is named by their `display_name`, or
  by the LOCAL PART of their email — never the whole address, which is what
  an account signs in with.
- **Join is addressed by TOKEN and leave by UUID.** Holding a live link is
  the whole access rule for joining, and the token is the only identifier a
  non-participant has been given; the uuid keys the participant-scoped
  endpoints, so a link-holder must never see one. `/trips/<uuid>/` 404s for
  them, and `/trips/s/<token>/` is their surface.
- **A signed-out recipient gets a sign-in link and nothing more.** SNOW-764
  needed `PENDING_SESSION_KEY`, a bounded per-browser pending list and two
  widened endpoints only because a shared route had nowhere to be *seen*
  before it was claimed. A trip has a page, and the page shows the plan
  before it asks for anything, so the whole pending-claim apparatus is
  unnecessary here.
- **Account erasure has to delete organised trips in Python.**
  `Trip.created_by` is `CASCADE`, and a bulk cascade runs no Python, so each
  trip's minted meeting-point `Location` would survive the account that
  created it. `apps.accounts.services.deletion.erase_account` calls
  `delete_trips_for_user` beside `delete_favourites_for_user` for exactly
  this reason.
- **`/trips/<uuid>/` is a page**, which is a third exception to
  [`two-documents-and-a-map`](two-documents-and-a-map.md). A trip is
  authored to be *sent*, so its link has to unfurl as a card and open as
  something a recipient can read before being asked for anything.
- **A trip is the first interactive multi-user object.** Everything else the
  map holds is one account's own data drawn on shared reference data. The
  next such object should read this file rather than re-argue it.
