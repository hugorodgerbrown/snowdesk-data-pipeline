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

Matching runs over the whole file rather than line by line, because
``djangofmt`` wraps a ``{% blocktrans %}`` body at column 120 and a claim
that straddles the wrap would otherwise walk through: the guard's first
draft missed "Your / subscription and account" in
``_pwa_reset_required.html`` for exactly that reason. Line numbers are
recovered from the match offset, so a failure still names the line.

``_ALLOWED`` is the escape hatch, and it is deliberately per-line: a
pattern loose enough to spare the legitimate strings would be loose
enough to miss the next false one. To add a line, say in the reason why
the thing it describes actually happens.

**What this guard does not cover.** Web Push is the hard case, because
some of it is true: a browser really does create a push subscription,
Snowdesk really does store it, and the privacy policy has to say so. The
patterns therefore discriminate rather than ban the word — "your
subscriptions" is caught, "your browser creates a subscription" is not.
Two consequences a reader should know about:

* A false promise phrased in Web Push's own vocabulary — "push
  subscriptions", "a subscription is created for you" — passes. Nothing
  short of reading the sentence can separate that from the true copy, so
  the guard does not try.
* A claim split across a sentence boundary, or worded with none of these
  phrases at all ("we'll be in touch when conditions change"), passes.
  The guard catches the phrasings the site actually used, not every
  phrasing it could.
* A bare noun or verb passes, because the true Web Push copy uses the
  same words. SNOW-877 fixed a ``"Subscribe"`` button label and a
  ``"Subscription saved."`` toast in the component library, and neither
  is caught: a pattern loose enough to reach them would fire on the push
  consent paragraph. Six of that ticket's eight strings are covered; those
  two are not, and are recorded here rather than counted as guarded.
* Each pattern matches one word order, so the same claim rearranged
  slips through. These were all checked and none of them fires:
  "manage the subscriptions on your account" (``your`` not adjacent, and
  outside the three-word budget), "manage subscriptions from your account
  settings" (no possessive before the noun), and "notifications are
  unlocked by installing" (``unlocks`` before the noun is what
  ``install-unlocks-notifications`` looks for). This is the narrowest and
  most foreseeable gap: a rewrite of a caught line can evade the pattern
  written for it. Prefer adding a pattern over trusting this list to
  stay short.
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

# Copy that reaches a reader without being a template. The component
# library renders its card, button, banner and status-page variants from
# these dicts, so a claim written here is a claim on a page — it is just
# a staff page. SNOW-877 found seven false strings in this one module
# that the template walk could never have seen, three of them outside
# the range the ticket had listed.
_EXTRA_COPY_FILES = [_ROOT / "apps" / "public" / "_component_fixtures.py"]

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
    # A subscription the reader owns. Snowdesk has none to offer: the
    # email subscription is retired (SNOW-802/805) and the only push
    # subscription is one the browser creates on a staff demo page. The
    # possessive is what discriminates — "your subscriptions" is a
    # promise, "your browser creates a subscription" is a description of
    # Web Push and true, so the intervening-word budget is short and an
    # article immediately before the noun disqualifies the match.
    "your-subscriptions": re.compile(
        r"\byour\s+(?:[\w'-]+\s+){0,3}(?<!a )(?<!an )(?<!the )subscriptions?\b",
        re.IGNORECASE,
    ),
    # A subscription listed among the things an account holds, which is
    # how the delete-account copy described one. Plural only, and not the
    # push or feed kind: both of those are real.
    "account-holds-subscriptions": re.compile(
        r"\b(saved|stored|holds|keeps|contains|delete|deletes|remove|removes)\b"
        r"[^.]{0,60}?(?<!push )(?<!rss )\bsubscriptions\b",
        re.IGNORECASE,
    ),
    # Installing to the home screen unlocks nothing: the service worker
    # registers on any page load, so a downloaded area reads offline in
    # the browser tab too, and there is no notification to unlock.
    # SNOW-877. The component library said "You'll receive alerts for the
    # regions below", "Get avalanche alerts" and "You have no active
    # subscriptions" — three phrasings none of the patterns above reach,
    # because each names the thing without the qualifying word they need.
    "receive-alerts": re.compile(
        r"\b(receive|receiving|get)\s+(?:[\w'-]+\s+){0,2}(alerts?|notifications?)\b",
        re.IGNORECASE,
    ),
    "avalanche-alerts": re.compile(
        r"\bavalanche\s+(alert|notification|digest)s?\b", re.IGNORECASE
    ),
    "count-of-subscriptions": re.compile(
        r"\b(no|active|any)\s+(?:active\s+)?subscriptions?\b", re.IGNORECASE
    ),
    "install-unlocks-notifications": re.compile(
        r"\bunlocks?\s+(?:[\w-]+\s+){0,3}(notification|alert|update|subscription)s?\b",
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
    "templates/_debug/push_demo.html": [
        (
            "All stored subscriptions",
            "Web Push subscriptions do exist and are stored — PushSubscription "
            "rows the browser created. This is the staff-only push demo page "
            "(every push_views route is @staff_member_required), so no reader "
            "is being offered anything.",
        ),
    ],
    "apps/public/_component_fixtures.py": [
        (
            "Bulletin updated.",
            "An ingest banner, not a notification. It sits in the admin set "
            "beside 'Bulletin processed successfully.' and \"We couldn't "
            'process this bulletin.", and a revised bulletin really is '
            "issued and ingested — the banner states that it happened, and "
            "promises nobody it will be told.",
        ),
    ],
}


# The copy the site actually carried, and the claim that must catch each
# line. Every entry is a verbatim string from a template as it stood
# before SNOW-707, so a pattern edited until it stops matching one of
# these has silently stopped guarding the surface that string came from.
_ORIGINALS: list[tuple[str, str]] = [
    (
        "bulletin-notification",
        "Sign in to manage your Snowdesk avalanche bulletin subscriptions.",
    ),
    (
        "daily-updates",
        "Avalanche bulletins for Switzerland, France, Austria, South Tyrol and "
        "Trentino. Receive daily bulletin updates, submit field observations.",
    ),
    (
        "email-notification",
        "Signing in unlocks extras such as email alerts and saved favourite locations.",
    ),
    (
        "subscribed-region",
        "Manage or remove your subscribed regions at any time from the account menu.",
    ),
    (
        "subscribe-to-a-region",
        "Once signed in, subscribe to a region from its bulletin page to get an "
        "email whenever a new bulletin is published for it.",
    ),
    ("unsubscribe", "Unsubscribe with one click from any alert email."),
    (
        "we-will-tell-you",
        "Add Snowdesk to your home screen for one-tap access and notifications "
        "when a new bulletin lands.",
    ),
    (
        "your-subscriptions",
        "This account link has expired or is invalid. Request a new one to "
        "manage your Snowdesk subscriptions.",
    ),
    (
        "your-subscriptions",
        "We've sent you an account link. Check your inbox to manage your "
        "Snowdesk subscriptions.",
    ),
    (
        "your-subscriptions",
        "If that address is registered, we've sent you a link to manage your "
        "subscriptions. It expires in 24 hours.",
    ),
    (
        "your-subscriptions",
        "Your email address is confirmed. You can manage your account and "
        "subscriptions from here.",
    ),
    (
        "your-subscriptions",
        "Snowdesk needs to reset local data on this device to keep working. "
        "Your subscription and account are stored on the server and are not "
        "affected.",
    ),
    (
        "account-holds-subscriptions",
        "Permanently deletes your account and everything saved to it — "
        "subscriptions, favourites, passkeys and any field reports you have "
        "submitted. This cannot be undone.",
    ),
    (
        "install-unlocks-notifications",
        "Tap the Share icon, then choose Add to Home Screen. Home-screen "
        "installs unlock notifications and offline access.",
    ),
    # SNOW-877 — the component library's card fixtures. Not templates, which
    # is why the walk never saw them until this ticket added the module to it.
    ("receive-alerts", "You'll receive alerts for the regions below."),
    ("avalanche-alerts", "Get avalanche alerts"),
    ("count-of-subscriptions", "You have no active subscriptions."),
]


def _template_files() -> list[Path]:
    """Return every file whose copy can reach a reader.

    The template roots, plus the non-template modules in
    :data:`_EXTRA_COPY_FILES` that hold rendered copy of their own.

    Returns:
        Paths to every ``.html`` and ``.txt`` template and every extra
        copy file, sorted, so a failure names the same file on every
        machine.

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
        source: The template's raw text.

    Returns:
        The same text with comment bodies replaced by their own newlines,
        so a reported line number still points at the offending line.

    """
    return _COMMENT_RE.sub(lambda m: "\n" * m.group().count("\n"), source)


def _allowed_reason(relative_path: str, context: str) -> str | None:
    """Return why this text is a known-true use of a claim phrase.

    Args:
        relative_path: The template's path relative to the repository root.
        context: The matching text plus the line it sits on, comments
            already stripped.

    Returns:
        The recorded reason when the text is listed in ``_ALLOWED`` for
        this template, otherwise None.

    """
    for fragment, reason in _ALLOWED.get(relative_path, []):
        if fragment in context:
            return reason
    return None


def _offences() -> list[str]:
    """Return one description per template passage that promises an alert.

    Patterns run against the whole file, not one line at a time: a
    ``{% blocktrans %}`` body is wrapped at column 120 by ``djangofmt``,
    so a claim regularly straddles two lines. Whitespace in every pattern
    is matched with a class that already spans a newline; the line number
    comes back from the match offset.

    Returns:
        Human-readable ``path:line: claim: text`` strings, empty when
        every template tells the truth.

    """
    found: list[str] = []
    for path in _template_files():
        relative_path = str(path.relative_to(_ROOT))
        source = _strip_comments(path.read_text(encoding="utf-8"))
        lines = source.splitlines()
        for claim, pattern in _CLAIMS.items():
            for match in pattern.finditer(source):
                number = source.count("\n", 0, match.start()) + 1
                matched = " ".join(match.group().split())
                line = lines[number - 1] if number <= len(lines) else ""
                if _allowed_reason(relative_path, f"{matched}\n{line}") is not None:
                    continue
                found.append(f"{relative_path}:{number}: {claim}: {matched}")
    return sorted(found)


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

    @pytest.mark.parametrize(("claim", "sample"), _ORIGINALS)
    def test_each_claim_pattern_matches_the_copy_it_was_written_for(
        self, claim: str, sample: str
    ) -> None:
        """Each pattern still catches the SNOW-707 string that motivated it.

        The patterns are the whole guard, and a typo in one is invisible —
        it fails nothing and reports nothing. These samples are the real
        sentences the site carried before SNOW-707 corrected them, so a
        pattern that stops matching one has stopped guarding the surface
        the string came from.
        """
        assert _CLAIMS[claim].search(sample), f"{claim} no longer matches its own copy"

    def test_every_pattern_has_a_sentence_it_was_written_for(self) -> None:
        """A pattern with no sample is a pattern nothing proves works.

        The first draft of this guard shipped patterns that matched none
        of five real strings, and nothing said so, because the samples
        were written to the patterns rather than the patterns to the copy.
        """
        covered = {claim for claim, _ in _ORIGINALS}

        assert covered == set(_CLAIMS), (
            f"claims with no sample sentence: {sorted(set(_CLAIMS) - covered)}"
        )

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
            "Your browser's push service (Google, Mozilla or Apple, depending on "
            "your browser) — if you turn on push notifications, your browser "
            "creates a subscription on its vendor's service and we send "
            "notifications through it. We never include bulletin content or your "
            "email address in a notification payload.",
            "Create a Snowdesk account to save favourites and share field reports.",
            "Enter your email address and we'll send you a link to verify it.",
            "If that address needs verifying, we've sent you a link to confirm "
            "it. It expires in 24 hours.",
            "Enter your email address and we'll send you a sign-in link.",
            "If that address is registered, we've sent you a link to sign in.",
            "Click the button below to sign in to your Snowdesk account.",
            "Enter your email address and we'll send you a link to choose a new "
            "password.",
            "If that address has a Snowdesk account with a password, we've sent "
            "you a link to reset it. It expires in 24 hours.",
            "Change the email address on your Snowdesk account.",
            "We've sent a confirmation link to your new Snowdesk email address.",
            "Verify your email to submit a field observation. Check your inbox "
            "for the verification link.",
            "Reports are shared with the community.",
            "subscribe a feed reader to the RSS feed for the country you follow",
            "Avalanche information across the Alps, sourced daily from SLF.",
        ]

        for text in legitimate:
            caught = [claim for claim, p in _CLAIMS.items() if p.search(text)]
            assert not caught, f"{caught} wrongly caught: {text}"

    def test_every_allowlist_entry_still_describes_a_real_line(self) -> None:
        """An allowlist entry outlives the copy it excused, and then hides.

        Each ``_ALLOWED`` fragment must still appear in the template it is
        recorded against. A stale one is a standing permission to write the
        claim back, granted for a reason nobody can check.
        """
        for relative_path, entries in _ALLOWED.items():
            source = _strip_comments(
                (_ROOT / relative_path).read_text(encoding="utf-8")
            )
            for fragment, reason in entries:
                assert fragment in source, (
                    f"{relative_path} no longer contains {fragment!r}, allowed "
                    f"because: {reason}"
                )
