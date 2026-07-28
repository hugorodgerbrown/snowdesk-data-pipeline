"""
regions/forms.py — Django forms for the regions application.

Provides:
  - ResortDetailsForm: the hand-curated Resort metadata fields (SNOW-500)
    edited from the in-map resort editor's side panel. The panel is a JSON
    client, not an HTML form post, so this form exists purely as the
    validation layer for ``public.api.edit_resort_save`` — it is never
    rendered. Using a ModelForm keeps every constraint (max_length, the
    MM-DD ``MONTH_DAY_VALIDATOR``, URL syntax, PositiveSmallInteger range)
    in one place: the model.
"""

from typing import Any

from django import forms

from .models import Resort

# The metadata fields the edit-resorts panel owns. Deliberately excludes
# ``latitude``/``longitude`` (handled separately by the save endpoint, which
# also auto-rebinds the parent region from the point), ``region``/``canton``
# (derived, not hand-typed), and the geocode-provenance columns (stamped by
# the endpoint, never sent by the client).
RESORT_DETAIL_FIELDS: tuple[str, ...] = (
    "name_alt",
    "operator_name",
    "website",
    "num_lifts",
    "num_runs",
    "total_piste_km",
    "base_elevation_m",
    "top_elevation_m",
    "typical_season_open",
    "typical_season_close",
)


class ResortDetailsForm(forms.ModelForm):
    """Validate the hand-curated Resort metadata fields.

    Bound against an existing ``Resort`` instance by the save endpoint. The
    endpoint merges the incoming JSON over the instance's current values
    before binding, so an absent key means "leave alone" rather than
    "blank it" — see ``public.api.edit_resort_save``.
    """

    class Meta:
        """Form metadata."""

        model = Resort
        fields = list(RESORT_DETAIL_FIELDS)

    def clean_total_piste_km(self) -> float | None:
        """Reject negative piste lengths.

        The model column is a plain ``FloatField`` (a negative distance is
        meaningless but not expressible in the column type), so the bound
        check lives here rather than on the model.
        """
        value: float | None = self.cleaned_data.get("total_piste_km")
        if value is not None and value < 0:
            raise forms.ValidationError("Total piste length cannot be negative.")
        return value

    def clean(self) -> dict[str, Any]:
        """Reject a top elevation that sits below the base elevation."""
        # ``ModelForm.clean`` is typed as returning an optional dict; the
        # base implementation always returns ``self.cleaned_data``.
        cleaned: dict[str, Any] = super().clean() or {}
        base = cleaned.get("base_elevation_m")
        top = cleaned.get("top_elevation_m")
        if base is not None and top is not None and top < base:
            self.add_error(
                "top_elevation_m",
                "Top elevation cannot be below the base elevation.",
            )
        return cleaned
