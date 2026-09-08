"""
tests/public/test_no_bulletin_alert_claims.py — no template may promise a
bulletin alert Snowdesk does not send (SNOW-707).

Snowdesk has never emailed anyone when a bulletin publishes. No task, no
command and no scheduled job exists that could, SNOW-875 deleted the last
of the machinery that looked as though it might, and SNOW-7 — the ticket
to build the digest — was cancelled, so the gap is permanent rather than
early. Web Push is not the escape hatch either: every ``push_views`` route
is ``@staff_member_required`` and the only client is the staff demo page,
so a promise of push notifications is as false as a promise of email.

The site said otherwise on **twenty-two** surfaces, including a live
transactional email and a banner rendered on every public page. Eleven of
them were in the ticket. The other eleven were found because a grep
happened to run beside them — nobody could have listed them from memory,
and nothing would have failed if they had been missed. That is the defect
this module exists to stop repeating: the claim is cheap to write, reads
as a feature, and no test anywhere else asserts its absence.

So the check is a grep, not a rendering. A false promise is false in the
source, in every locale, and on a page no test client visits — and the
next one will be written by someone who never read this file.

Comments are stripped before matching. ``templates/includes/nav.html``
describes a menu whose subscribed-region links SNOW-802 removed, and a
comment about history is not a claim to a reader.

``_ALLOWED`` is the escape hatch, and it is deliberately per-line: a
pattern loose enough to spare the legitimate strings would be loose
enough to miss the next false one. To add a line, say in the reason why
the thing it describes actually happens.
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

# Django comments, replaced by their own newlines so line numbers survive.
_COMMENT_RE = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}", re.DOTALL)

# The claims. Each one is a phrase that can only be true if Snowdesk tells
# a reader about a bulletin it did not ask for, which it never does.
_CLAIMS: dict[str, re.Pattern[str]] = {
    "bulletin-notification": re.compile(
        r"bulletin\s+(notification|alert|subscription|update|digest)", re.IGNORECASE
    ),
    "daily-updates": re.compile(
        r"daily\s+(bulletin|region|avalanche)\s*\w*\s+(update|alert|digest|email)",
        re.IGNORECASE,
    ),
    "email-notification": re.compile(
        r"email\s+(notification|alert)|alert\s+email", re.IGNORECASE
    ),
    "subscribed-region": re.compile(r"subscribed\s+region", re.IGNORECASE),
    "subscribe-to-a-region": re.compile(
        r"subscrib\w*\s+(to|for)\s+(a\s+|the\s+|one\s+)?"
        r"(region|bulletin|update|alert|email|notification)",
        re.IGNORECASE,
    ),
    "unsubscribe": re.compile(r"unsubscrib", re.IGNORECASE),
    "we-will-tell-you": re.compile(
        r"(email|notify|alert|tell)\s+you\s+(when|whenever|as soon as)"
        r"|(get|receive)\s+an?\s+(email|alert|notification)\s+(when|whenever)"
        r"|notifications?\s+when\s+a\s+new",
        re.IGNORECASE,
    ),
}

# Lines that match a pattern and are nonetheless true. Keyed by the
# template's repository-relative path; each entry is a substring of the
# offending line and the reason the thing it describes really happens.
_ALLOWED: dict[str, list[tuple[str, str]]] = {
    "apps/public/templates/public/help/_topic_accounts.html": [
        (
            "Snowdesk sends no bulletin alerts",
            "A denial, not a promise: the help page states the absence "
            "outright so a reader stops looking for the setting.",
        ),
    ],
}


def _template_files() -> list[Path]:
    """Return every template file under the project's template roots.

    Returns:
        Paths to every ``.html`` and ``.txt`` template, sorted, so a
        failure names the same file on every machine.

    """
    return sorted(
        path
        for root in _TEMPLATE_ROOTS
        for path in root.rglob("*")
        if path.suffix in _TEMPLATE_SUFFIXES and path.is_file()
    )


def _strip_comments(source: str) -> str:
    """Blank out Django comments, keeping every line number intact.

    Args:
        source: The template's raw text.

    Returns:
        The same text with comment bodies replaced by their own newlines,
        so a reported line number still points at the offending line.

    """
    return _COMMENT_RE.sub(lambda m: "\n" * m.group().count("\n"), source)


def _is_allowed(relative_path: str, line: str) -> bool:
    """Report whether this line is a known-true use of a claim phrase.

    Args:
        relative_path: The template's path relative to the repository root.
        line: The matching line, comments already stripped.

    Returns:
        True when the line is listed in ``_ALLOWED`` for this template.

    """
    return any(
        fragment in line for fragment, _reason in _ALLOWED.get(relative_path, [])
    )


def _offences() -> list[str]:
    """Return one description per template line that promises an alert.

    Returns:
        Human-readable ``path:line: claim: text`` strings, empty when
        every template tells the truth.

    """
    found: list[str] = []
    for path in _template_files():
        relative_path = str(path.relative_to(_ROOT))
        source = _strip_comments(path.read_text(encoding="utf-8"))
        for number, line in enumerate(source.splitlines(), start=1):
            if _is_allowed(relative_path, line):
                continue
            for claim, pattern in _CLAIMS.items():
                if pattern.search(line):
                    found.append(f"{relative_path}:{number}: {claim}: {line.strip()}")
    return found


class TestNoBulletinAlertClaims:
    """No template offers to tell a reader about a new bulletin."""

    def test_no_template_promises_a_bulletin_alert(self) -> None:
        """Every claim phrase is absent, or listed in ``_ALLOWED`` with a reason.

        The fix for a failure here is the copy, not the allowlist. Snowdesk
        sends five emails — a sign-in link, an address verification, a
        password reset, and the confirmation and notice for an email
        change — and none of them mentions a bulletin. If a page needs to
        answer "how do I hear about a new bulletin", the true answer is
        the per-country RSS feed (``apps/public/feeds.py``).
        """
        offences = _offences()

        assert not offences, "templates promise bulletin alerts:\n" + "\n".join(
            offences
        )

    def test_the_guard_reads_the_templates_it_claims_to(self) -> None:
        """A guard that walks an empty tree passes for the wrong reason.

        SNOW-557 moved every app under ``apps/``; a future move would
        leave the globs matching nothing and this module green over a site
        full of false promises.
        """
        files = _template_files()

        assert len(files) > 100, f"only {len(files)} templates found"
        assert any(str(path).endswith("public/privacy.html") for path in files), (
            "the privacy policy is not being read"
        )
        assert any(
            str(path).endswith("includes/_pwa_install_prompt.html") for path in files
        ), "the project-level template root is not being read"

    @pytest.mark.parametrize(
        ("claim", "sample"),
        [
            ("bulletin-notification", "Manage your avalanche bulletin subscriptions."),
            ("daily-updates", "Receive daily bulletin updates."),
            ("email-notification", "Optional email notifications for your regions."),
            ("subscribed-region", "Remove your subscribed regions at any time."),
            ("subscribe-to-a-region", "Once signed in, subscribe to a region."),
            ("unsubscribe", "Unsubscribe with one click from any alert email."),
            (
                "we-will-tell-you",
                "Install Snowdesk for notifications when a new bulletin lands.",
            ),
        ],
    )
    def test_each_claim_pattern_matches_the_copy_it_was_written_for(
        self, claim: str, sample: str
    ) -> None:
        """Each pattern still catches the SNOW-707 string that motivated it.

        The patterns are the whole guard, and a typo in one is invisible —
        it fails nothing and reports nothing. These samples are the real
        sentences the site carried before SNOW-707 corrected them.
        """
        assert _CLAIMS[claim].search(sample), f"{claim} no longer matches its own copy"

    def test_the_legitimate_strings_are_not_caught(self) -> None:
        """The true things the site says must survive the guard.

        Push consent is described conditionally in the privacy policy and
        is accurate; the transactional emails do send; and the help page
        points readers at the RSS feeds, which is the honest answer to the
        question the deleted copy was answering.
        """
        legitimate = [
            "Push notifications — consent. Your browser asks you before a "
            "subscription is created, and turning notifications off removes it.",
            "Enter your email address and we'll send you a link to verify it.",
            "If that address is registered, we've sent you a link to sign in.",
            "Click the button below to sign in to your Snowdesk account.",
            "subscribe a feed reader to the RSS feed for the country you follow",
            "Avalanche information across the Alps, sourced daily from SLF.",
        ]

        for text in legitimate:
            caught = [claim for claim, p in _CLAIMS.items() if p.search(text)]
            assert not caught, f"{caught} wrongly caught: {text}"
