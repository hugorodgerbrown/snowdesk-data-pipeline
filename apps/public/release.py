"""
apps/public/release.py — the release string a person reads.

Snowdesk carries two version identities, and they answer different
questions:

* ``settings.APP_VERSION`` — a git SHA. Changes on every deploy, names one
  build exactly. This is what the PWA update check compares, what
  ``APP_BLOCKED_VERSIONS`` matches against, and what busts the ETags. A
  machine reads it; nobody says it out loud.
* ``settings.APP_RELEASE`` — the ordinal of the production release, from the
  tracked ``VERSION`` file. This is what goes in the account menu.

This module owns the one thing neither setting can express on its own: how
to render them together for the tier the code happens to be running on.

Production shows the release and nothing else — ``v24``. A production user
reporting a problem needs to name their version, and the release number is
the whole answer, because every production build of release 24 is the same
build.

Everywhere else appends the short build id — ``v24 · fce4f14``. Staging
deploys on every merge to ``main``, so it sits somewhere between releases:
the release number alone would name a release staging is already ahead of,
and the SHA is the part you actually need when triaging what is on there
right now. The separator is a middle dot rather than a slash or a dash so
it cannot be misread as part of either value.

The tier is decided by ``PWAEnvironmentIdentity.from_settings()`` — the same
signal that already swaps the PWA name, icon set and theme colour — so
there is one answer to "is this production" rather than two that can drift.
"""

from __future__ import annotations

from django.conf import settings

from apps.public.site_environment import PWAEnvironmentIdentity

# How much of the SHA to show. Seven characters is the abbreviation git and
# GitHub both use, and it is long enough to paste into `git show`.
SHORT_BUILD_LENGTH = 7


def short_build(version: str) -> str:
    """Abbreviate a build identifier for display.

    Args:
        version: The raw ``APP_VERSION`` value — a full git SHA on a
            deployed environment, something short and human ("dev")
            elsewhere.

    Returns:
        The first seven characters when the value is a git SHA; the value
        unchanged otherwise, since we have no idea what it means and
        truncating it could destroy it.

    """
    build = str(version or "").strip()
    is_sha = len(build) >= 12 and all(c in "0123456789abcdefABCDEF" for c in build)
    return build[:SHORT_BUILD_LENGTH] if is_sha else build


def release_label() -> str:
    """Return the version string for the account menu.

    Returns:
        ``"v24"`` on production, ``"v24 · fce4f14"`` off it, and ``""``
        when no release number is configured — an unnumbered build shows
        no version rather than a bare ``v`` or a lone SHA that reads like
        one. The template hides the row on the empty string.

    """
    release = str(getattr(settings, "APP_RELEASE", "") or "").strip()
    if not release:
        return ""

    label = f"v{release}"
    if PWAEnvironmentIdentity.from_settings().is_production:
        return label

    build = short_build(getattr(settings, "APP_VERSION", ""))
    return f"{label} · {build}" if build else label
