"""
apps/public/release.py — the release string a person reads.

Snowdesk carries two version identities, and they answer different
questions:

* ``settings.APP_VERSION`` — a git SHA. Changes on every deploy, names one
  build exactly. This is what the PWA update check compares, what
  ``APP_BLOCKED_VERSIONS`` matches against, and what busts the ETags. A
  machine reads it; nobody says it out loud.
* ``settings.APP_RELEASE`` — the ordinal of the production release, from the
  tracked ``VERSION`` file. This is what a person means by "which version
  are you on?", and it is what the site footer shows.

This module renders the second one — ``v24`` — for the footer, on every
tier.

SNOW-769 removed the short build id the label used to append off
production. It was there because staging deploys on every merge and sits
between releases, so the SHA named what was actually on there. But the
label moved from the account menu to the site footer, where every visitor
sees it, and a SHA is not for them: ``AppVersionHeaderMiddleware`` stamps
the full one on ``X-App-Version`` on every single response, which is a
better answer for the person who wants it and no answer at all for the
person who does not.
"""

from __future__ import annotations

from django.conf import settings


def release_label() -> str:
    """Return the version string for the site footer.

    Returns:
        ``"v24"``, or ``""`` when no release number is configured — an
        unnumbered build shows no version rather than a bare ``v``. The
        footer hides the row on the empty string.

    """
    release = str(getattr(settings, "APP_RELEASE", "") or "").strip()
    return f"v{release}" if release else ""
