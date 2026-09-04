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

**The meeting point is a pin on a map**, and these two coordinate fields
are what that pin writes into. They shipped briefly as the whole control
and that was a defect: a coordinate is not something an organiser knows.
A group agrees to meet at the top car park, and asking them to express
that as 46.080012, 7.318197 asks them for a fact they would have to go and
look up.

The fields stay for three reasons the map cannot cover — a keyboard-only
visitor cannot drag a marker, someone holding a coordinate from elsewhere
would rather paste it than hunt for the same spot by eye, and a visitor
with no JavaScript has nothing else. They are still what the form posts and
validates, so none of that path changed; only what sits in front of them
did. See ``apps/trips/templates/trips/partials/_trip_meeting_picker.html``
and ``static/js/trip_meeting_picker.js``.

The two ``data-meeting-*`` attributes are how the picker finds these
fields. They are attributes rather than ids because the form is re-rendered
wholesale by an HTMX swap on a validation error, and the picker re-queries
the document on every write rather than holding a node that a swap may
already have detached.
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
        widget=forms.NumberInput(
            attrs={
                "class": _INPUT_CLASSES,
                "step": "any",
                "data-meeting-latitude": "",
            }
        ),
    )
    longitude = forms.FloatField(
        min_value=_LONGITUDE_MIN,
        max_value=_LONGITUDE_MAX,
        widget=forms.NumberInput(
            attrs={
                "class": _INPUT_CLASSES,
                "step": "any",
                "data-meeting-longitude": "",
            }
        ),
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
