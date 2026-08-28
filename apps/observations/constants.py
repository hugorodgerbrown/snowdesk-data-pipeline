"""
apps/observations/constants.py — shared field-observation constants.

Holds the values two apps need to agree on. Its own module rather than a
name in ``apps.observations.views`` so ``apps.public.views`` can import it
without reaching into the view layer — the same split, for the same reason,
as ``apps.routes.constants`` and ``apps.favourites.constants`` (whose note
explains the import cycle that forced it there first).

``OBSERVATION_LIST_MAP_VARIANT`` is the ``?variant=`` value that tells
``observations.views.observation_list`` it is answering the map's panel, so
each row's label renders as a control that frames its report (SNOW-752).
``apps.public.views`` appends it to the list URL it hands the map page.

The routes and favourites lists spend the same parameter on a whole
template; this one has a single template and spends it on one flag. That is
a difference in what the variant buys, not in the contract — a caller asks
for the map's shape by the same name on all three, which is the point of
having a name for it.
"""

OBSERVATION_LIST_MAP_VARIANT = "map"
