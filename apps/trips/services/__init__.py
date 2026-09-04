"""
apps/trips/services/ — business logic for the trips application (SNOW-819).

One module per concern, in the order a trip travels through them:

- ``trips.py`` — create, update and delete a trip, and the per-user cap.
- ``shares.py`` — mint and revoke the one share link a trip has (SNOW-821).
- ``participants.py`` — join, leave, and the roster (SNOW-822).
- ``routes.py`` — copy a trip's snapshot into a viewer's own routes
  (SNOW-824).
"""
