"""
tests/core/test_choices_are_upper_case.py — Repo-wide TextChoices casing guard.

SNOW-582 established a convention: every ``TextChoices`` field this
codebase owns stores UPPER CASE values, so a value read straight out of a
table — psql, a query result, a CSV export — is visually distinguishable
from free text, user input, or third-party data. This test walks every
model field with ``choices`` across our own apps and asserts every choice
value is upper case, with one explicit exemption list for fields whose
values are an external wire vocabulary this codebase does not control.

Exempt (external EAWS/CAAML vocabularies — values arrive from upstream
providers and must round-trip unchanged):

- ``RegionDayRating.min_rating`` / ``max_rating`` / ``am_rating`` /
  ``pm_rating`` — the EAWS danger-rating scale (``"no_rating"``, ``"low"``,
  … ``"very_high"``).

Not walked at all (by construction, not by exemption): the three
``TextChoices`` classes in ``apps/bulletins/schema.py``
(``DangerRatingValue``, ``ValidTimePeriod``, ``AvalancheProblemType``) are
CAAML wire vocabularies used inline in dataclasses, never attached as a
model field's ``choices`` — so they never appear in the
``apps.get_models()`` walk below and need no exemption entry. Likewise
third-party apps (django-csp's ``CspRule.directive`` /
``CspReportBlacklist.directive``) are excluded by only walking models
whose module path starts with ``"apps."``.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import models

# (app_label, model_name, field_name) triples exempt from the UPPER CASE
# convention — external EAWS wire vocabulary, not this codebase's own choice.
_EXEMPT_FIELDS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("bulletins", "regiondayrating", "min_rating"),
        ("bulletins", "regiondayrating", "max_rating"),
        ("bulletins", "regiondayrating", "am_rating"),
        ("bulletins", "regiondayrating", "pm_rating"),
    }
)


def _own_model_choice_fields() -> list[tuple[str, str, str, list[Any]]]:
    """Return (app_label, model_name, field_name, choice_values) for every
    CharField/TextField carrying ``choices`` on a model this project owns.

    Restricted to models whose module path starts with ``"apps."`` so
    third-party apps (django-csp, django-waffle, django_tasks_db, …) are
    never walked — their choice vocabularies are not this codebase's to
    convert.
    """
    found: list[tuple[str, str, str, list[Any]]] = []
    for model in apps.get_models():
        if not model.__module__.startswith("apps."):
            continue
        model_name = model._meta.model_name or model.__name__.lower()
        for field in model._meta.get_fields():
            if not isinstance(field, models.Field):
                continue
            choices = getattr(field, "choices", None)
            if not choices:
                continue
            values = [value for value, _label in choices]
            found.append((model._meta.app_label, model_name, field.name, values))
    return found


class TestChoiceValuesAreUpperCase:
    """Every in-scope TextChoices member value is its own upper-case form."""

    def test_every_non_exempt_choice_field_is_upper_case(self) -> None:
        """Walks apps.get_models() and asserts UPPER CASE, exemptions aside."""
        violations: list[str] = []

        for app_label, model_name, field_name, values in _own_model_choice_fields():
            if (app_label, model_name, field_name) in _EXEMPT_FIELDS:
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                if value != value.upper():
                    violations.append(
                        f"{app_label}.{model_name}.{field_name}: {value!r}"
                    )

        assert not violations, (
            "Found lower-case TextChoices value(s) outside the exempt list "
            "(SNOW-582 convention): " + ", ".join(violations)
        )

    def test_exempt_fields_still_exist_on_their_models(self) -> None:
        """Guards the exemption list itself against a renamed/removed field.

        If a field in ``_EXEMPT_FIELDS`` is renamed or removed, this fails
        loudly rather than letting the exemption silently stop applying to
        anything.
        """
        present = {
            (app_label, model_name, field_name)
            for app_label, model_name, field_name, _values in (
                _own_model_choice_fields()
            )
        }
        missing = _EXEMPT_FIELDS - present
        assert not missing, f"Exempt field(s) no longer exist: {missing}"
