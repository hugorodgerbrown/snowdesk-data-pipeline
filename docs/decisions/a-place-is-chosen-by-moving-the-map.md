---
name: a-place-is-chosen-by-moving-the-map
description: _place_pin.html, place_picker.js, trip_meeting_picker.js, trips:what3words, meeting_place.py; a meeting point never shows lat/lon
status: current
last-reviewed: 2026-10-24
---

# A place is chosen by moving the map, and a meeting point is named in words

## Decision

Wherever someone chooses a point on a map, the pin stays still over the
frame and the map moves under it. The coordinate is the map's centre,
read on `moveend`. The map page's favourite and field-report flows have
done this since SNOW-475 (`window.PlacePicker`, `static/js/place_picker.js`).
SNOW-1214 brought the Plan page's meeting point onto the same model
(`static/js/trip_meeting_picker.js`). Both draw the same glyph,
`templates/includes/_place_pin.html`.

A trip's meeting point never shows its latitude and longitude, on the
Plan page, the trip page, the share page or the trip list. It is named by
its three word address, looked up live as the map settles
(`trips:what3words`) and again on save. Beside the address, and in its
place when there is none, goes one sentence placing the point against the
route: "At the route's start", "On the route, 9 km in", "2 km north-east
of the route's start". The sentence comes from
`apps/trips/services/meeting_place.py` on the server and
`static/js/meeting_place_core.js` in the browser. When there is no
address, an "Open in maps" link carries the coordinate in its href.

## Why

A dragged marker sits under the finger placing it. On an iPad the
meeting-point marker did not always land where it was tapped. SNOW-840 had
also left no way to place it from a keyboard. A still pin is always exactly
where it looks, and the arrow keys already pan a MapLibre map, so keyboard
users get placement back for free. A tap eases the map to that spot. The
two quick picks, Route start and Route end, cover the common answers.

A group agrees to meet at the top car park, not at 46.080012, 7.318197.
The numbers answer no question anyone in the group is asking. The address
names the 3 m square, and the sentence says where it is relative to the
day.

## Consequences

- The sentence's rule exists twice. `tests/fixtures/meeting_place_vectors.json`
  is read by both `tests/trips/test_meeting_place.py` and
  `tests/js/test_meeting_place_core.js`, so changing the rule on one side
  only fails a test.
- The live lookup is signed in, flag-gated (`what3words`) and limited to
  60 a minute per user. It stores nothing; the saved `Location` is
  converted again on save.
- `_three_word_address.html` takes a `place` (and `maps_url`). A caller
  that passes one never renders a coordinate, title included. Favourites
  pass none and keep the coordinate fallback.
- Edit-resorts placement still uses a draggable marker, on purpose. It is a
  staff tool used with a mouse (`docs/map-and-api.md`, "Edit-resorts mode").
