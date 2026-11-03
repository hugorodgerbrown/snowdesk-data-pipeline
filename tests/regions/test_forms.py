"""
tests/regions/test_forms.py — Tests for apps.regions.forms.

ResortEditForm (SNOW-1401) is ResortDetailsForm plus ``name``, ``notes``
and ``needs_review``; these tests pin that it keeps every inherited rule
and validates the three fields it adds.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.regions.forms import RESORT_DETAIL_FIELDS, RESORT_EDIT_FIELDS, ResortEditForm
from apps.regions.models import Resort
from tests.factories import ResortFactory


def _data(resort: Resort, **changes: Any) -> dict[str, Any]:
    """Return the form data for ``resort`` with ``changes`` merged over it."""
    return {
        field: changes.get(field, getattr(resort, field))
        for field in RESORT_EDIT_FIELDS
    }


def test_fields_are_the_details_plus_three() -> None:
    """The edit form adds exactly name, notes and needs_review."""
    assert set(RESORT_EDIT_FIELDS) - set(RESORT_DETAIL_FIELDS) == {
        "name",
        "notes",
        "needs_review",
    }


@pytest.mark.django_db
class TestResortEditForm:
    """ResortEditForm: the model's constraints over the editable fields."""

    def test_accepts_a_valid_edit(self) -> None:
        """A rename, a note and the review flag validate."""
        resort = ResortFactory.create(name="Old")
        form = ResortEditForm(
            _data(resort, name="New", notes="Per the 2024 atlas.", needs_review=True),
            instance=resort,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["needs_review"] is True

    def test_blank_name_is_refused(self) -> None:
        """A resort always has a name."""
        resort = ResortFactory.create()
        form = ResortEditForm(_data(resort, name=""), instance=resort)
        assert not form.is_valid()
        assert "name" in form.errors

    def test_inherits_top_above_base(self) -> None:
        """The details form's elevation rule still applies."""
        resort = ResortFactory.create()
        form = ResortEditForm(
            _data(resort, base_elevation_m=2000, top_elevation_m=1500), instance=resort
        )
        assert not form.is_valid()
        assert "top_elevation_m" in form.errors
