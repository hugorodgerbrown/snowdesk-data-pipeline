"""
tests/test_template_comments.py — no Django template opens a ``{#`` comment it
does not close on the same line.

Django's ``{# … #}`` comment is single-line only: one that runs on to a second
line is not a comment at all, and its text is printed on the page. SNOW-1479
found one printing "{# Rendered only when there IS an address…" on every
observation row, and a second in the favourite card. A comment that needs
more than one line is a ``{% comment %}`` block.

Covers every ``.html`` and ``.txt`` file under the project's template
directories: ``templates/`` and each app's ``templates/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_FILES = sorted(
    path
    for pattern in ("templates/**/*", "apps/*/templates/**/*")
    for path in ROOT.glob(pattern)
    if path.suffix in {".html", ".txt"} and path.is_file()
)
UNCLOSED = re.compile(r"\{#(?!.*#\})")


def _unclosed_comments(path: Path) -> list[int]:
    """Return the line numbers in ``path`` that open a ``{#`` left unclosed."""
    return [
        number
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if UNCLOSED.search(line)
    ]


def test_templates_were_found() -> None:
    """The glob reaches the template tree, so the check below is not vacuous."""
    assert len(TEMPLATE_FILES) > 100


@pytest.mark.parametrize(
    "path", TEMPLATE_FILES, ids=lambda path: str(path.relative_to(ROOT))
)
def test_every_comment_closes_on_its_own_line(path: Path) -> None:
    """A ``{#`` with no ``#}`` after it on the same line prints as text."""
    assert _unclosed_comments(path) == [], path.relative_to(ROOT)
