"""
tests/pipeline/test_utils.py — Tests for pipeline utility functions.

Covers html_to_markdown conversion of headings, paragraphs, line breaks,
bold/italic, ordered/unordered lists, nested tags, and edge cases.
"""

from pipeline.utils import html_to_markdown


class TestHtmlToMarkdown:
    """Tests for html_to_markdown."""

    def test_empty_string(self):
        """Empty input returns empty output."""
        assert html_to_markdown("") == ""

    def test_none_returns_empty(self):
        """None input returns empty output."""
        assert html_to_markdown(None) == ""

    def test_plain_text_unchanged(self):
        """Text without HTML tags is returned as-is."""
        assert html_to_markdown("Hello world") == "Hello world"

    def test_h1(self):
        """h1 tag converts to # heading."""
        assert html_to_markdown("<h1>Title</h1>") == "# Title"

    def test_h2(self):
        """h2 tag converts to ## heading."""
        assert html_to_markdown("<h2>Subtitle</h2>") == "## Subtitle"

    def test_h3(self):
        """h3 tag converts to ### heading."""
        assert html_to_markdown("<h3>Section</h3>") == "### Section"

    def test_h6(self):
        """h6 tag converts to ###### heading."""
        assert html_to_markdown("<h6>Deep</h6>") == "###### Deep"

    def test_paragraph(self):
        """Paragraph tags produce double newlines."""
        result = html_to_markdown("<p>First</p><p>Second</p>")
        assert "First" in result
        assert "Second" in result
        assert "\n\n" in result

    def test_br_tag(self):
        """br tag produces a single newline."""
        result = html_to_markdown("Line one<br>Line two")
        assert "Line one\nLine two" == result

    def test_self_closing_br(self):
        """Self-closing br tag produces a single newline."""
        result = html_to_markdown("Line one<br/>Line two")
        assert "Line one\nLine two" == result

    def test_bold_strong(self):
        """strong tag wraps text in **."""
        assert html_to_markdown("<strong>bold</strong>") == "**bold**"

    def test_bold_b(self):
        """b tag wraps text in **."""
        assert html_to_markdown("<b>bold</b>") == "**bold**"

    def test_italic_em(self):
        """em tag wraps text in *."""
        assert html_to_markdown("<em>italic</em>") == "*italic*"

    def test_italic_i(self):
        """i tag wraps text in *."""
        assert html_to_markdown("<i>italic</i>") == "*italic*"

    def test_unordered_list(self):
        """ul/li converts to - prefixed items."""
        html = "<ul><li>One</li><li>Two</li></ul>"
        result = html_to_markdown(html)
        assert "- One" in result
        assert "- Two" in result

    def test_ordered_list(self):
        """ol/li converts to numbered items."""
        html = "<ol><li>First</li><li>Second</li><li>Third</li></ol>"
        result = html_to_markdown(html)
        assert "1. First" in result
        assert "2. Second" in result
        assert "3. Third" in result

    def test_mixed_content(self):
        """A mix of headings, paragraphs, and bold is converted correctly."""
        html = "<h2>Weather</h2><p>It will be <strong>sunny</strong> tomorrow.</p>"
        result = html_to_markdown(html)
        assert "## Weather" in result
        assert "**sunny**" in result
        assert "tomorrow." in result

    def test_strips_unknown_tags(self):
        """Tags without special handling are stripped, keeping text."""
        result = html_to_markdown("<div><span>Hello</span></div>")
        assert result == "Hello"

    def test_collapses_excessive_newlines(self):
        """Three or more consecutive newlines are collapsed to two."""
        html = "<p>A</p><p></p><p>B</p>"
        result = html_to_markdown(html)
        assert "\n\n\n" not in result

    def test_strips_leading_trailing_whitespace(self):
        """Output has no leading or trailing whitespace."""
        result = html_to_markdown("  <p>  Hello  </p>  ")
        assert result == "Hello"
