"""
apps/public/templatetags/map_focus.py — the coordinate a panel row is framed by.

One tag, ``{% focus_target %}``, which formats WGS-84 degrees into the string
``includes/_ugc_panel_row.html`` hands to ``static/js/row_focus.js`` as
``data-row-focus``: two ordinates for a point ("lon,lat"), four for a bbox
("west,south,east,north").

Why a tag and not ``{{ favourite.longitude }}``.  Django renders a float
through the active locale, so a template that interpolates one directly emits
``7,53`` under a German UI — a decimal comma that ``Number()`` reads as NaN
and that a comma-separated attribute cannot even be split apart again.  The
formatting therefore happens in Python, through ``%f``, which is
locale-independent by construction.  ``|stringformat:"f"`` would do the same
job for one ordinate; a tag does it for two or four and joins them, so the
three row templates that need this share one definition rather than four
chained filters each.

``%f`` is six decimal places — about 11 cm at the equator, well under the
precision of anything Snowdesk stores — so no caller has to choose a
precision, and every row's attribute has the same shape.

Used by ``routes/partials/_route.html``, ``favourites/partials/_favourite.html``
and ``observations/partials/_observation.html``, each on the map variant of its
list only.  Registered in ``apps.public`` because that is the only app in the
project with a ``templatetags`` package; Django's tag registry is global, so
the owning app is a matter of where the file lives, not of who may load it.
"""

from typing import Any

from django import template

register = template.Library()


@register.simple_tag
def focus_target(*ordinates: Any) -> str:
    """Format WGS-84 ordinates into a ``data-row-focus`` value.

    ``Any`` rather than ``float`` because the arguments come from a template
    variable, which resolves to whatever the context holds — a float, an
    ``int``, ``None``, or the empty string the engine substitutes for a
    missing key.  Narrowing the annotation would describe the intent while
    misdescribing what actually arrives, and the guards below exist for
    precisely the values it would exclude.

    Args:
        *ordinates: Two ordinates (longitude, latitude) for a point, or four
            (west, south, east, north) for a GeoJSON bbox.  Any other count
            is a caller error and yields the empty string, which renders a
            row with no focus button rather than one that flies the map to a
            half-read coordinate.

    Returns:
        The ordinates as comma-separated ``%f`` decimals, or ``""`` when the
        count is wrong or any ordinate is missing.  ``None`` is a real case —
        a route stores its bbox as JSON, so an index past its end resolves to
        the template engine's empty string rather than raising.

    """
    if len(ordinates) not in (2, 4):
        return ""
    values: list[str] = []
    for ordinate in ordinates:
        if ordinate is None or ordinate == "":
            return ""
        try:
            values.append(f"{float(ordinate):f}")
        except TypeError, ValueError:
            return ""
    return ",".join(values)
