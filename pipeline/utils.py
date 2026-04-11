"""
pipeline/utils.py — Shared utility functions for the pipeline application.

Provides text-processing helpers used across views, admin, and services.
"""

import re
from html.parser import HTMLParser

# Simple open-tag → literal-prefix mapping. Tags that require state
# (headings, lists, list items) are handled as special cases.
_SIMPLE_OPEN_TAGS: dict[str, str] = {
    "p": "\n\n",
    "br": "\n",
    "strong": "**",
    "b": "**",
    "em": "*",
    "i": "*",
}
_HEADING_TAGS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LIST_CONTAINER_TAGS: frozenset[str] = frozenset({"ul", "ol"})


class _HTMLToMarkdownParser(HTMLParser):
    """
    A simple HTML-to-Markdown converter.

    Handles headings (h1–h6), paragraphs, line breaks, bold, italic,
    unordered/ordered lists, and strips all other tags. Produces clean
    Markdown text suitable for plain-text display.
    """

    def __init__(self) -> None:
        """Initialise the parser with empty output state."""
        super().__init__()
        self._output: list[str] = []
        self._current_tag: str = ""
        self._list_type: list[str] = []
        self._list_counter: list[int] = []

    def _open_list_item(self) -> None:
        """Emit the prefix for an opening ``<li>`` tag."""
        if self._list_type and self._list_type[-1] == "ol":
            self._list_counter[-1] += 1
            self._output.append(f"\n{self._list_counter[-1]}. ")
        else:
            self._output.append("\n- ")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """
        Handle an opening HTML tag.

        Args:
            tag: The tag name (lowercase).
            attrs: List of (attribute, value) pairs.

        """
        self._current_tag = tag

        if tag in _SIMPLE_OPEN_TAGS:
            self._output.append(_SIMPLE_OPEN_TAGS[tag])
        elif tag in _HEADING_TAGS:
            level = int(tag[1])
            self._output.append(f"\n\n{'#' * level} ")
        elif tag in _LIST_CONTAINER_TAGS:
            self._list_type.append(tag)
            self._list_counter.append(0)
        elif tag == "li":
            self._open_list_item()

    def handle_endtag(self, tag: str) -> None:
        """
        Handle a closing HTML tag.

        Args:
            tag: The tag name (lowercase).

        """
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._output.append("\n")
        elif tag in ("strong", "b"):
            self._output.append("**")
        elif tag in ("em", "i"):
            self._output.append("*")
        elif tag in ("ul", "ol"):
            if self._list_type:
                self._list_type.pop()
            if self._list_counter:
                self._list_counter.pop()
            self._output.append("\n")

        self._current_tag = ""

    def handle_data(self, data: str) -> None:
        """
        Handle raw text content between tags.

        Args:
            data: The text content.

        """
        self._output.append(data)

    def get_output(self) -> str:
        """
        Return the accumulated Markdown text, cleaned up.

        Returns:
            A string with normalised whitespace and no leading/trailing blanks.

        """
        text = "".join(self._output)
        # Collapse runs of 3+ newlines into 2.
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(html: str) -> str:
    """
    Convert an HTML string to Markdown.

    Handles headings (h1–h6 → #–######), paragraphs, line breaks,
    bold/italic, and ordered/unordered lists. All other tags are
    stripped, leaving only their text content.

    Args:
        html: The HTML string to convert.

    Returns:
        A Markdown-formatted plain-text string.

    """
    if not html:
        return ""

    parser = _HTMLToMarkdownParser()
    parser.feed(html)
    return parser.get_output()
