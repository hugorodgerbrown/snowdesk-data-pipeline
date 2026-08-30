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

There is deliberately NO constant here for the SNOW-764 ``route_sharing``
waffle flag, though it is asked for in two modules. Flag names are written
as literals at the ``flag_is_active`` call site because
``tests/core/services/test_waffle_manifest_call_sites.py`` reads the source
to check every manifest entry has a reader and every reader has a manifest
entry — and it can only see literals. A constant would make the flag
invisible to that guard, which is the check standing between a rename and
a feature that is silently off in every environment.
"""

ROUTE_LIST_MAP_VARIANT = "map"
