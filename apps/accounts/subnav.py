"""
apps/accounts/subnav.py — Sub-nav model for the account area (SNOW-667).

``/account/`` was one 529-line page stacking nine unranked sections. SNOW-667
split it into child routes tied together by a persistent sub-nav, grouped by
what the user is doing: "Your data" (things they have saved) and "Settings"
(things they can change about the account).

The groups live here as data rather than as markup in the partial, so adding
a child route is one entry in ``_GROUPS`` and nothing else. That matters
because three tickets are queued to do exactly that — SNOW-668
(``/account/places/``), SNOW-677 (``/account/observations/``) and, whenever it
is picked up, a routes page for the ``apps.routes`` model that already exists.

Rendered by ``accounts/partials/_account_subnav.html``.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from django.urls import reverse
from django.utils.translation import gettext_lazy as _


class _Entry(NamedTuple):
    """One child route in the account sub-nav.

    Attributes:
        url_name: Fully-qualified URL name, resolved at render time.
        label: Link text shown to the user.

    """

    url_name: str
    label: Any


class _Group(NamedTuple):
    """A labelled cluster of sub-nav entries.

    Attributes:
        label: Group heading.
        entries: Entries rendered under it, in order.

    """

    label: Any
    entries: tuple[_Entry, ...]


# The account area's shape. Adding a child route means adding an ``_Entry``
# here — the partial iterates whatever this produces and needs no edit.
#
# "Your data" holds one entry today because the hub still carries the
# subscriptions and favourites that SNOW-668 will lift into
# ``/account/places/``. It is a group of one rather than a bare link so that
# the second and third entries do not require re-shaping the nav.
_GROUPS: tuple[_Group, ...] = (
    _Group(
        label=_("Your data"),
        entries=(_Entry(url_name="accounts:hub", label=_("Overview")),),
    ),
    _Group(
        label=_("Settings"),
        entries=(_Entry(url_name="accounts:settings", label=_("Settings")),),
    ),
)


def build_subnav(current_url_name: str) -> list[dict[str, Any]]:
    """Build the account sub-nav, marking the page currently being rendered.

    Args:
        current_url_name: Fully-qualified URL name of the page being
            rendered (e.g. ``"accounts:settings"``). An unrecognised name
            marks nothing as current, which is the right outcome for a page
            that sits outside the nav — the sub-nav still renders, it just
            highlights nothing.

    Returns:
        A list of group dicts, each with a ``label`` and a list of ``entries``
        carrying ``label``, resolved ``url`` and a boolean ``current``.

    """
    return [
        {
            "label": group.label,
            "entries": [
                {
                    "label": entry.label,
                    "url": reverse(entry.url_name),
                    "current": entry.url_name == current_url_name,
                }
                for entry in group.entries
            ],
        }
        for group in _GROUPS
    ]
