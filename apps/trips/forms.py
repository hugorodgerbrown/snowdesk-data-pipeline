"""
apps/trips/forms.py — the one form a trip is authored through (SNOW-820).

``TripForm`` backs both halves of authoring: creating a trip from a route
the organiser already owns, and editing the plan afterwards. One form
rather than two because the editable fields are identical — the SNAPSHOT is
not editable and has no field here, deliberately (see ``Trip``'s docstring
and ``update_trip``).

**The date and time are wall-clock**, so both widgets are plain HTML5
``date`` / ``time`` inputs and nothing here converts anything. A trip at
07:30 is at 07:30 for everyone on it, whatever timezone their phone is set
to.

**The meeting point is two number inputs, not a pin drop.** Dropping a pin
means arming the map page's placement machinery
(``static/js/place_picker.js``, ``PlacementFocus``) off the map page, which
is real surgery for a control that has a working default: the fields are
prefilled with the route's first coordinate, which is where a group meets
far more often than not. A map-based override is a follow-up, not a
prerequisite.
"""

from __future__ import annotations

from django import forms

from apps.public.templatetags.components import input_classes

# The shared text-input chrome, reached through the design system's own tag
# rather than restated here — see apps/accounts/forms.py for the same read
# and SNOW-672 for why the string lives in one place.
_INPUT_CLASSES = input_classes(size="standard")

# Must match Trip.name's max_length.
_NAME_MAX_LENGTH = 100

# WGS-84 bounds. Enforced so a typo in a hand-edited coordinate is a form
# error rather than a Location row pointing off the planet.
_LATITUDE_MIN = -90.0
_LATITUDE_MAX = 90.0
_LONGITUDE_MIN = -180.0
_LONGITUDE_MAX = 180.0


class TripForm(forms.Form):
    """The organiser's plan: a day, a time, a label, a note and a place.

    Every field except the two coordinates is optional-or-defaulted at the
    model, so the only genuinely required pair is ``date`` and
    ``start_time`` — a trip with no day and no meeting time is not a trip.
    """

    date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": _INPUT_CLASSES,
            }
        ),
    )
    start_time = forms.TimeField(
        widget=forms.TimeInput(
            attrs={
                "type": "time",
                "class": _INPUT_CLASSES,
            }
        ),
    )
    name = forms.CharField(
        max_length=_NAME_MAX_LENGTH,
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASSES}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": _INPUT_CLASSES, "rows": 4}),
    )
    latitude = forms.FloatField(
        min_value=_LATITUDE_MIN,
        max_value=_LATITUDE_MAX,
        widget=forms.NumberInput(attrs={"class": _INPUT_CLASSES, "step": "any"}),
    )
    longitude = forms.FloatField(
        min_value=_LONGITUDE_MIN,
        max_value=_LONGITUDE_MAX,
        widget=forms.NumberInput(attrs={"class": _INPUT_CLASSES, "step": "any"}),
    )

    def clean_name(self) -> str:
        """Strip surrounding whitespace from the optional label."""
        name: str = self.cleaned_data.get("name", "")
        return name.strip()

    def clean_description(self) -> str:
        """Strip surrounding whitespace from the optional note.

        Not otherwise sanitised, and deliberately so: the note is the
        organiser's own prose and is rendered through Django's
        auto-escaping like any other user-supplied string. Nothing in this
        app calls ``mark_safe`` on it.
        """
        description: str = self.cleaned_data.get("description", "")
        return description.strip()
