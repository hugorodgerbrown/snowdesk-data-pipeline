"""
tests/public/templatetags/test_snowdesk_html.py — Tests for the snowdesk_html filter.

Covers sanitisation behaviour (tag allowlist, attribute stripping, disallowed
tag removal), edge cases (None/empty input), return type guarantees, and a
template-integration smoke test.  One test case uses a real SLF prose sample
from ``sample_data/sample_variable_day.json`` to guard against regressions
with actual field data.
"""

import json
from pathlib import Path

import pytest
from django.template import Context, Template
from django.utils.safestring import SafeString

from public.templatetags.snowdesk_html import snowdesk_html

# Absolute path to the sample data fixture used in the real-SLF test.
_SAMPLE_PATH = (
    Path(__file__).resolve().parents[3] / "sample_data" / "sample_variable_day.json"
)


@pytest.fixture
def slf_snowpack_comment() -> str:
    """Return the real snowpackStructure.comment from the variable-day sample."""
    with _SAMPLE_PATH.open() as fh:
        data = json.load(fh)
    props = data.get("properties", data)
    comment: str = props["snowpackStructure"]["comment"]
    return comment


@pytest.fixture
def slf_weather_forecast_comment() -> str:
    """Return the real weatherForecast.comment from the variable-day sample."""
    with _SAMPLE_PATH.open() as fh:
        data = json.load(fh)
    props = data.get("properties", data)
    comment: str = props["weatherForecast"]["comment"]
    return comment


class TestSnowdeskHtmlAllowlistedTags:
    """Allowlisted tags survive sanitisation unchanged."""

    def test_allowlisted_tags_pass_through(self):
        """Structural tags in the allowlist round-trip verbatim."""
        html = "<h1>Snow</h1><p>prose</p>"
        result = snowdesk_html(html)
        assert result == html

    def test_nested_allowlisted_content(self):
        """Nested allowlisted tags round-trip verbatim."""
        html = "<ul><li><strong>a</strong></li></ul>"
        result = snowdesk_html(html)
        assert result == html

    def test_em_tag_passes_through(self):
        """The ``em`` tag is in the allowlist and must survive."""
        html = "<p><em>critical</em> terrain</p>"
        result = snowdesk_html(html)
        assert result == html

    def test_h2_tag_passes_through(self):
        """The ``h2`` tag is in the allowlist and must survive."""
        html = "<h2>Fresh snow</h2><p>-</p>"
        result = snowdesk_html(html)
        assert result == html


class TestSnowdeskHtmlDisallowedTags:
    """Disallowed tags are stripped (not escaped) from the output."""

    def test_script_tag_stripped(self):
        """
        A ``<script>`` tag wrapper is stripped; allowlisted content remains.

        bleach's ``strip=True`` removes the tag delimiters but keeps inner text
        as inert plain text — ``alert(1)`` cannot execute as JavaScript when
        rendered as a text node.  The important guarantee is that the ``<script>``
        element itself is gone so the browser never interprets the content as a
        script block.
        """
        html = "<script>alert(1)</script><p>ok</p>"
        result = snowdesk_html(html)
        assert "<script" not in result
        assert "<p>ok</p>" in result

    def test_div_wrapper_stripped(self):
        """A ``<div>`` wrapper is stripped; its text content remains."""
        html = "<div><p>text</p></div>"
        result = snowdesk_html(html)
        assert "<div" not in result
        assert "<p>text</p>" in result

    def test_anchor_tag_stripped(self):
        """``<a>`` tags are not in the allowlist and are stripped."""
        html = '<p>See <a href="https://slf.ch">SLF</a>.</p>'
        result = snowdesk_html(html)
        assert "<a" not in result
        assert "SLF" in result


class TestSnowdeskHtmlAttributeStripping:
    """All attributes are removed from allowlisted tags."""

    def test_class_and_onclick_stripped(self):
        """``class`` and ``onclick`` attributes are stripped from a ``<p>`` tag."""
        html = '<p class="foo" onclick="x()">text</p>'
        result = snowdesk_html(html)
        assert result == "<p>text</p>"

    def test_contenteditable_stripped(self):
        """``contenteditable`` is stripped — this appears in real SLF weather data."""
        html = '<h2 contenteditable="false">Fresh snow</h2>'
        result = snowdesk_html(html)
        assert result == "<h2>Fresh snow</h2>"
        assert "contenteditable" not in result


class TestSnowdeskHtmlEdgeCases:
    """Edge cases: None input, empty string, return type."""

    def test_none_input_returns_empty_safestring(self):
        """``None`` input returns an empty ``SafeString``."""
        result = snowdesk_html(None)
        assert result == ""
        assert isinstance(result, SafeString)

    def test_empty_string_returns_empty_safestring(self):
        """An empty string input returns an empty ``SafeString``."""
        result = snowdesk_html("")
        assert result == ""
        assert isinstance(result, SafeString)

    def test_return_type_is_safestring(self):
        """The return type is always ``SafeString`` so Django does not re-escape it."""
        result = snowdesk_html("<p>hello</p>")
        assert isinstance(result, SafeString)

    def test_none_is_safestring(self):
        """``None`` path also returns ``SafeString`` (not plain ``str``)."""
        result = snowdesk_html(None)
        assert isinstance(result, SafeString)


class TestSnowdeskHtmlRealSlfSample:
    """Validates the filter against real SLF prose fields from the sample fixture."""

    def test_snowpack_comment_sanitises_without_error(self, slf_snowpack_comment: str):
        """The real snowpackStructure comment sanitises without raising."""
        result = snowdesk_html(slf_snowpack_comment)
        assert isinstance(result, SafeString)

    def test_snowpack_comment_preserves_allowlisted_tags(
        self, slf_snowpack_comment: str
    ):
        """After sanitisation the ``<h1>`` and ``<p>`` tags from SLF are still present."""
        result = snowdesk_html(slf_snowpack_comment)
        assert "<h1>" in result
        assert "<p>" in result

    def test_weather_forecast_contenteditable_stripped(
        self, slf_weather_forecast_comment: str
    ):
        """
        The real weatherForecast comment contains ``<h2 contenteditable="false">``.

        After sanitisation the attribute must be gone and the tag preserved.
        """
        result = snowdesk_html(slf_weather_forecast_comment)
        assert "contenteditable" not in result
        assert "<h2>" in result
        assert isinstance(result, SafeString)


class TestSnowdeskHtmlTemplateIntegration:
    """Template-integration test: filter registered and works inside a template."""

    def test_script_stripped_in_template_context(self):
        """
        Rendering the filter inside a template strips the ``<script>`` tag.

        The tag delimiters are removed so the browser never interprets the
        content as a script block.  bleach leaves inner text as inert plain
        text which is safe to render.
        """
        tmpl = Template("{% load snowdesk_html %}{{ val|snowdesk_html }}")
        ctx = Context({"val": "<script>x</script><p>ok</p>"})
        rendered = tmpl.render(ctx)
        assert "<script" not in rendered
        assert "<p>ok</p>" in rendered

    def test_none_in_template_context_renders_empty(self):
        """``None`` passed through the template filter renders as an empty string."""
        tmpl = Template("{% load snowdesk_html %}{{ val|snowdesk_html }}")
        ctx = Context({"val": None})
        rendered = tmpl.render(ctx)
        assert rendered == ""

    def test_allowlisted_html_not_re_escaped(self):
        """Allowlisted tags are not entity-escaped by Django's auto-escaping."""
        tmpl = Template("{% load snowdesk_html %}{{ val|snowdesk_html }}")
        ctx = Context({"val": "<p>hello</p>"})
        rendered = tmpl.render(ctx)
        # If auto-escaping hit the output the tag would be &lt;p&gt;...
        assert rendered == "<p>hello</p>"
        assert "&lt;" not in rendered
