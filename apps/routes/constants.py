"""
apps/routes/constants.py — shared routes constants.

Holds the values two apps need to agree on. Its own module rather than a
name in ``apps.routes.views`` so ``apps.public.views`` can import it without
reaching into the view layer — the same split, for the same reason, as
``apps.favourites.constants`` (whose note explains the import cycle that
forced it there first).

``ROUTE_LIST_MAP_VARIANT`` is the ``?variant=`` value that selects the map
sheet's lean row template in ``routes.views.route_list`` (SNOW-686).
``apps.public.views`` appends it to the list URL it hands the map page; the
view maps it back to a template.
"""

ROUTE_LIST_MAP_VARIANT = "map"
