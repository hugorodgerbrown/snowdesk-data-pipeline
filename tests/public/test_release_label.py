"""
tests/public/test_release_label.py — the version string in the site footer.

Snowdesk carries two version identities and they must not be confused:
``APP_VERSION`` is a git SHA that identifies a build to a machine (the PWA
update check, ``APP_BLOCKED_VERSIONS``, ETags), and ``APP_RELEASE`` is the
release ordinal a person reads. ``apps.public.release`` renders the second.

What is pinned here:

* The label is the release and nothing else, on every tier. SNOW-769
  dropped the short build id that used to be appended off production: the
  label moved to the footer, where every visitor sees it, and the full SHA
  is on ``X-App-Version`` for anyone who actually wants it.
* An unnumbered build renders nothing at all, so the footer omits the
  label rather than showing a bare "v" or a dangling separator.
* The footer shows it to **anonymous** visitors — the move out of the
  account menu is the point of SNOW-769, since the people most likely to
  be asked which version they are on are the ones not signed in.
* It stays a label rather than becoming a control, and keeps the
  screen-reader-only "Version" prefix that names what the string is.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from apps.public.release import release_label

PRODUCTION = {"SITE_ENVIRONMENT": "production"}
STAGING = {"SITE_ENVIRONMENT": "staging"}
SHA = "fce4f140ce343af5c8bf20d5792bebc453165984"


class TestReleaseLabel:
    """The rendered string."""

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_production_shows_the_release(self) -> None:
        """Every production build of release 24 is the same build.

        So the release number is the whole answer, and a SHA beside it is
        noise a user would have to read out.
        """
        assert release_label() == "v24"

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **STAGING)
    def test_staging_shows_the_same_string(self) -> None:
        """The tier no longer changes the answer (SNOW-769).

        Staging used to append the short SHA, because it deploys on every
        merge and sits between releases. The footer is not the place for
        that: ``X-App-Version`` carries the full SHA on every response,
        which is what triage should be reading anyway.
        """
        assert release_label() == "v24"

    @override_settings(APP_RELEASE="24", APP_VERSION="dev", **STAGING)
    def test_the_build_id_never_reaches_the_label(self) -> None:
        """Not even a short, readable one like a local checkout's "dev"."""
        assert release_label() == "v24"

    @override_settings(APP_RELEASE="", APP_VERSION=SHA, **PRODUCTION)
    def test_no_release_number_renders_nothing(self) -> None:
        """An unnumbered build shows no version rather than a bare "v"."""
        assert release_label() == ""

    @override_settings(APP_RELEASE="  24  ", APP_VERSION=SHA, **PRODUCTION)
    def test_surrounding_whitespace_is_stripped(self) -> None:
        """The value comes from a file, and files end in newlines."""
        assert release_label() == "v24"


@pytest.mark.django_db
class TestSiteFooterVersion:
    """The label itself, in the global footer."""

    def _signed_in_client(self) -> Client:
        """Return a client signed in as an ordinary user.

        Returns:
            A ``Client`` with an authenticated session — used to prove the
            account menu no longer carries the version.

        """
        user = get_user_model().objects.create_user(
            username="rider@example.com",
            email="rider@example.com",
            password="not-a-real-password",  # noqa: S106 - test fixture
        )
        client = Client()
        client.force_login(user)
        return client

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_an_anonymous_visitor_sees_the_version(self, client: Client) -> None:
        """The SNOW-769 behaviour change.

        The label used to live in the account menu, which meant the people
        most likely to be asked "which version are you on?" — anyone not
        signed in — had no way to answer.
        """
        body = client.get("/").content.decode("utf-8")

        assert 'data-testid="site-footer-version"' in body
        assert "v24" in body

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_it_renders_inside_the_footer(self, client: Client) -> None:
        """Not merely somewhere on the page — inside the footer element."""
        body = client.get("/").content.decode("utf-8")
        footer = body[body.index('data-testid="site-footer"') :]
        footer = footer[: footer.index("</footer>")]

        assert "v24" in footer

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_it_keeps_its_screen_reader_prefix(self, client: Client) -> None:
        """A bare "v24" does not say what it is.

        The visible string is short because the footer is, but a
        screen-reader user gets the word "Version" in front of it.
        """
        body = client.get("/").content.decode("utf-8")
        row = body[
            body.index('data-testid="site-footer-version"') : body.index("v24") + 3
        ]

        assert "sr-only" in row
        assert "Version" in row

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_the_account_menu_no_longer_carries_it(self) -> None:
        """SNOW-769 moved the row, it did not copy it.

        The account menu is scoped to everything before the footer, so a
        version string found there would be a genuine duplicate.
        """
        body = self._signed_in_client().get("/").content.decode("utf-8")
        before_footer = body[: body.index('data-testid="site-footer"')]

        assert "v24" not in before_footer

    @override_settings(APP_RELEASE="", APP_VERSION=SHA, **PRODUCTION)
    def test_no_release_number_leaves_no_dangling_separator(
        self, client: Client
    ) -> None:
        """No number, no label — and no middot left hanging after Colophon."""
        body = client.get("/").content.decode("utf-8")
        footer = body[body.index('data-testid="site-footer"') :]
        footer = footer[: footer.index("</footer>")]
        after_last_link = footer[footer.rindex("</a>") :]

        assert 'data-testid="site-footer-version"' not in footer
        assert "&middot;" not in after_last_link
