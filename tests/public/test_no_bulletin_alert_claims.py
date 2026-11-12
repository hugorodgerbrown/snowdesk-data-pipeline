"""
tests/public/test_no_bulletin_alert_claims.py — no page may deny the bulletin
alerts Snowdesk sends (SNOW-1361; was SNOW-707's no-promise guard).

**The premise flipped when alerts went public.** SNOW-707 wrote this guard
when Snowdesk sent no bulletin alerts: it rejected copy that *promised*
one, because twenty-two surfaces did and nothing else asserted their
absence. SNOW-937 built alerts behind the ``bulletin_alerts`` flag, and
SNOW-1361 opened them to everyone, so those promises are now true and the
promise patterns are gone. What can now be false is the opposite claim:
copy that says Snowdesk does *not* send alerts, as the help page and
/compare/ did until SNOW-1361 changed them.

So the guard keeps a truth invariant rather than an exemption list: no
template, and no module that holds rendered copy, may deny alerts. The
help page and the /compare/ matrix pass because their copy is now true,
not because they are excused.

The check is a grep, not a rendering, for SNOW-707's reason: a false
claim is false in the source, in every locale, and on a page no test
client visits. Django comments are stripped first — a comment about
history is not a claim to a reader — and matching runs over the whole
file, because ``djangofmt`` wraps a ``{% blocktrans %}`` body at column
120 and a claim straddling the wrap would otherwise walk through.

**What this guard does not cover.** A denial worded with none of these
phrases ("we won't be in touch when conditions change") passes. The guard
catches the phrasings the site actually used; prefer adding a pattern to
trusting that list to stay short.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repository root: tests/public/<this file> → up three.
_ROOT = Path(__file__).resolve().parents[2]

# Every template tree that can reach a reader: the project-level one and
# each app's own. Nothing else renders to a person.
_TEMPLATE_ROOTS = [_ROOT / "templates", *sorted((_ROOT / "apps").glob("*/templates"))]

_TEMPLATE_SUFFIXES = frozenset({".html", ".txt"})

# Copy that reaches a reader without being a template: the component
# library's fixtures and registry prose (SNOW-877), and the /compare/
# matrix, whose cells and notes render as the comparison table (SNOW-1361).
_EXTRA_COPY_FILES = [
    _ROOT / "apps" / "design_system" / "_component_fixtures.py",
    _ROOT / "apps" / "design_system" / "design_tokens.py",
    _ROOT / "apps" / "public" / "competitor_matrix.py",
]

# Django comments, replaced by their own newlines so line numbers survive.
_COMMENT_RE = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}", re.DOTALL)

# The denials. Each one is a phrase that is false now that Snowdesk sends
# bulletin alerts.
_DENIALS: dict[str, re.Pattern[str]] = {
    "sends-no-bulletin-alerts": re.compile(
        r"sends\s+no\s+bulletin\s+alerts", re.IGNORECASE
    ),
    "no-bulletin-alerts": re.compile(r"\bno\s+bulletin\s+alerts\b", re.IGNORECASE),
    "does-not-send-alerts": re.compile(
        r"\b(doesn't|doesn’t|does\s+not)\s+send\s+(?:[\w-]+\s+){0,2}alerts\b",
        re.IGNORECASE,
    ),
    # /compare/'s own wording before SNOW-1361.
    "alerting-not-done": re.compile(
        r"alerting,?\s+which\s+it\s+does\s+not\s+do", re.IGNORECASE
    ),
}

# The copy the site actually carried, and the denial that must catch each
# one, so a pattern edited until it stops matching has visibly stopped
# guarding the surface it was written for.
_ORIGINALS: list[tuple[str, str]] = [
    (
        "sends-no-bulletin-alerts",
        "Snowdesk sends no bulletin alerts — the only email it will send you is "
        "a sign-in link, a password reset, or a confirmation that you changed "
        "your address.",
    ),
    ("no-bulletin-alerts", "There are no bulletin alerts on Snowdesk."),
    ("does-not-send-alerts", "Snowdesk does not send alerts."),
    ("does-not-send-alerts", "Snowdesk doesn't send bulletin alerts."),
    (
        "alerting-not-done",
        "Weakest at alerting, which it does not do at all; observed "
        "weather-station readings.",
    ),
]


def _copy_files() -> list[Path]:
    """Return every file whose copy can reach a reader.

    Returns:
        Paths to every ``.html`` and ``.txt`` template and every extra copy
        file, sorted, so a failure names the same file on every machine.

    """
    return sorted(
        [
            path
            for root in _TEMPLATE_ROOTS
            for path in root.rglob("*")
            if path.suffix in _TEMPLATE_SUFFIXES and path.is_file()
        ]
        + [path for path in _EXTRA_COPY_FILES if path.is_file()]
    )


def _strip_comments(source: str) -> str:
    """Blank out Django comments, keeping every line number intact.

    Args:
        source: The file's raw text.

    Returns:
        The same text with comment bodies replaced by their own newlines.

    """
    return _COMMENT_RE.sub(lambda m: "\n" * m.group().count("\n"), source)


def _denials_in(relative_path: str, source: str) -> list[str]:
    """Return one ``path:line: denial: text`` string per denial in a file.

    Args:
        relative_path: The file's path relative to the repository root.
        source: Its text, comments already stripped.

    Returns:
        The offences, empty when the file tells the truth.

    """
    found: list[str] = []
    for name, pattern in _DENIALS.items():
        for match in pattern.finditer(source):
            number = source.count("\n", 0, match.start()) + 1
            matched = " ".join(match.group().split())
            found.append(f"{relative_path}:{number}: {name}: {matched}")
    return found


def _offences() -> list[str]:
    """Return every passage, in every copy file, that denies alerts.

    Returns:
        The offences, sorted.

    """
    found: list[str] = []
    for path in _copy_files():
        source = _strip_comments(path.read_text(encoding="utf-8"))
        found += _denials_in(str(path.relative_to(_ROOT)), source)
    return sorted(found)


class TestNoBulletinAlertDenials:
    """No page tells a reader that Snowdesk sends no bulletin alerts."""

    def test_no_copy_denies_bulletin_alerts(self) -> None:
        """Every denial phrase is absent. The fix for a failure is the copy."""
        offences = _offences()

        assert not offences, "copy denies bulletin alerts:\n" + "\n".join(offences)

    def test_the_guard_reads_the_files_it_claims_to(self) -> None:
        """A guard that walks an empty tree passes for the wrong reason."""
        files = _copy_files()

        assert len(files) > 100, f"only {len(files)} templates found"
        assert any(str(p).endswith("public/help/_topic_accounts.html") for p in files)
        assert any(str(p).endswith("includes/_pwa_install_prompt.html") for p in files)
        assert any(str(p).endswith("public/competitor_matrix.py") for p in files), (
            "the /compare/ matrix is not being read"
        )

    @pytest.mark.parametrize(("denial", "sample"), _ORIGINALS)
    def test_each_denial_pattern_matches_the_copy_it_was_written_for(
        self, denial: str, sample: str
    ) -> None:
        """Each pattern still catches the sentence that motivated it."""
        assert _DENIALS[denial].search(sample), f"{denial} no longer matches"

    def test_every_pattern_has_a_sentence_it_was_written_for(self) -> None:
        """A pattern with no sample is a pattern nothing proves works."""
        assert {denial for denial, _ in _ORIGINALS} == set(_DENIALS)

    def test_a_denial_in_a_template_is_caught(self) -> None:
        """A regression sample: the old help sentence fails the guard again."""
        source = (
            "<p>{% blocktrans trimmed %}Everything except a field observation "
            "stays that way. Snowdesk sends no\n bulletin alerts &mdash; the only "
            "email{% endblocktrans %}</p>"
        )

        offences = sorted(_denials_in("sample.html", _strip_comments(source)))

        assert offences == [
            "sample.html:1: no-bulletin-alerts: no bulletin alerts",
            "sample.html:1: sends-no-bulletin-alerts: sends no bulletin alerts",
        ]

    def test_a_denial_in_a_comment_is_not_a_claim(self) -> None:
        """History in a comment is not copy a reader sees."""
        source = "{# Snowdesk sends no bulletin alerts, before SNOW-1361 #}\n<p>Hi</p>"

        assert _denials_in("sample.html", _strip_comments(source)) == []

    def test_the_true_copy_is_not_caught(self) -> None:
        """The alerts copy that replaced the denials passes."""
        legitimate = [
            "Snowdesk can tell you when the avalanche bulletin changes for a place "
            "you have saved. Alerts are off until you turn them on.",
            "Level or problem changes for saved places, by push or email",
            "Apart from alerts, the only email Snowdesk sends is a sign-in link.",
            "the feed does not send it again",
        ]

        for text in legitimate:
            caught = [name for name, p in _DENIALS.items() if p.search(text)]
            assert not caught, f"{caught} wrongly caught: {text}"
