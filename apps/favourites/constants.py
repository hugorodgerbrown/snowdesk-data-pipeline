"""
apps/favourites/constants.py — shared favourites constants.

Holds the values two apps need to agree on. Its own module rather than a
name in ``apps.favourites.views`` because ``apps.favourites.views`` imports
``apps.public.views`` at module level, so the reverse import would be a
cycle: this module imports nothing.

``FAVOURITE_LIST_MAP_VARIANT`` is the ``?variant=`` value that selects the
map sheet's lean row template in ``favourites.views.favourite_list``
(SNOW-658). ``apps.public.views`` appends it to the list URL it hands the
map page; the view maps it back to a template.
"""

FAVOURITE_LIST_MAP_VARIANT = "map"
