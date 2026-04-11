"""
tests/public/test_hazard_icons.py — Tests for the hazard_icons template filter.

Verifies that CAAML problem types map to the correct SVG icon paths and
that unknown types return an empty string.
"""

import pytest
from django.template import Context, Template

from public.templatetags.hazard_icons import hazard_icon


class TestHazardIconFilter:
    """Tests for the hazard_icon filter function."""

    @pytest.mark.parametrize(
        "problem_type,expected_fragment",
        [
            ("gliding_snow", "Gliding-Snow"),
            ("new_snow", "New-Snow"),
            ("persistent_weak_layers", "Persistent-Weak-Layer"),
            ("wet_snow", "Wet-Snow"),
            ("wind_slab", "Wind-Slab"),
            ("no_distinct_avalanche_problem", "No-Distinct-Avalanche-Problem"),
            ("cornices", "Cornices"),
        ],
    )
    def test_known_types_return_svg_path(
        self, problem_type: str, expected_fragment: str
    ):
        """Each known problem type maps to an SVG path containing its name."""
        result = hazard_icon(problem_type)

        assert result.startswith("icons/svg/")
        assert result.endswith(".svg")
        assert expected_fragment in result

    def test_unknown_type_returns_empty(self):
        """An unrecognised problem type returns an empty string."""
        assert hazard_icon("unknown_problem") == ""

    def test_empty_string_returns_empty(self):
        """An empty string returns an empty string."""
        assert hazard_icon("") == ""

    def test_filter_works_in_template(self):
        """The filter can be used inside a Django template."""
        template = Template("{% load hazard_icons %}{{ problem_type|hazard_icon }}")
        context = Context({"problem_type": "wind_slab"})
        rendered = template.render(context)

        assert "Wind-Slab" in rendered
        assert rendered.endswith(".svg")
