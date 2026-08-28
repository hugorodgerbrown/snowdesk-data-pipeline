"""
tests/public/test_release_label.py — the version string in the account menu.

Snowdesk carries two version identities and they must not be confused:
``APP_VERSION`` is a git SHA that identifies a build to a machine (the PWA
update check, ``APP_BLOCKED_VERSIONS``, ETags), and ``APP_RELEASE`` is the
release ordinal a person reads. ``apps.public.release`` renders the second
one, borrowing from the first only where it helps.

What is pinned here:

* Production shows the release alone; every other tier appends the short
  build id, because staging sits between releases and the SHA is the part
  that identifies what is actually on there.
* An unnumbered build renders nothing at all, so the menu omits the row
  rather than showing a bare "v".
* A non-SHA build id (local "dev") survives intact — abbreviating a value
  we cannot identify would destroy it.
* The menu itself shows the label, and shows it as a label rather than as a
  menu item: a focusable control that does nothing is a dead stop in the
  menu's tab order.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from apps.public.release import release_label, short_build

PRODUCTION = {"SITE_ENVIRONMENT": "production"}
STAGING = {"SITE_ENVIRONMENT": "staging"}
SHA = "fce4f140ce343af5c8bf20d5792bebc453165984"


class TestShortBuild:
    """A build id is abbreviated only when we can tell it is a SHA."""

    def test_sha_is_abbreviated_to_seven(self) -> None:
        """Seven characters — git's own abbreviation, and enough to paste."""
        assert short_build(SHA) == "fce4f14"

    def test_a_non_sha_is_left_alone(self) -> None:
        """A local build says "dev", which is not a SHA.

        Truncating it to seven characters would leave it intact by luck;
        truncating a shorter one would destroy it. Neither is abbreviated.
        """
        assert short_build("dev") == "dev"

    def test_a_short_hex_string_is_left_alone(self) -> None:
        """Below the SHA length threshold, hex or not, it stays whole.

        A deploy identifier could legitimately be a short hex-looking
        token; only something SHA-length is safe to assume is a SHA.
        """
        assert short_build("abc123") == "abc123"

    def test_empty_stays_empty(self) -> None:
        """No build id, no string — the caller decides what to do about it."""
        assert short_build("") == ""


class TestReleaseLabel:
    """The rendered string, per tier."""

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_production_shows_the_release_alone(self) -> None:
        """Every production build of release 24 is the same build.

        So the release number is the whole answer, and a SHA beside it is
        noise a user would have to read out.
        """
        assert release_label() == "v24"

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **STAGING)
    def test_staging_appends_the_build(self) -> None:
        """Staging is somewhere between releases, so the SHA is the answer."""
        assert release_label() == "v24 · fce4f14"

    @override_settings(APP_RELEASE="24", APP_VERSION="dev", **STAGING)
    def test_local_build_id_passes_through(self) -> None:
        """A local checkout says "dev", which is more useful than nothing."""
        assert release_label() == "v24 · dev"

    @override_settings(APP_RELEASE="24", APP_VERSION="", **STAGING)
    def test_no_build_id_falls_back_to_the_release(self) -> None:
        """A missing SHA costs the suffix and not the label."""
        assert release_label() == "v24"

    @override_settings(APP_RELEASE="", APP_VERSION=SHA, **PRODUCTION)
    def test_no_release_number_renders_nothing(self) -> None:
        """An unnumbered build shows no version rather than a bare "v"."""
        assert release_label() == ""

    @override_settings(APP_RELEASE="  24  ", APP_VERSION=SHA, **PRODUCTION)
    def test_surrounding_whitespace_is_stripped(self) -> None:
        """The value comes from a file, and files end in newlines."""
        assert release_label() == "v24"

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, SITE_ENVIRONMENT="typo")
    def test_an_unrecognised_environment_is_not_production(self) -> None:
        """Matching ``PWAEnvironmentIdentity``: only "production" is.

        A misconfigured tier showing a SHA it did not need is harmless; a
        misconfigured tier silently claiming to be production is not.
        """
        assert release_label() == "v24 · fce4f14"


@pytest.mark.django_db
class TestAccountMenu:
    """The row itself, in the signed-in navigation."""

    def _client(self) -> Client:
        """Return a client signed in as an ordinary user.

        Returns:
            A ``Client`` with an authenticated session — the version row
            lives inside the account menu, which anonymous visitors never
            see.

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
    def test_version_is_shown_in_the_menu(self) -> None:
        """The answer to "which version are you on?" is in the menu."""
        body = self._client().get("/").content.decode("utf-8")

        assert "v24" in body

    @override_settings(APP_RELEASE="24", APP_VERSION=SHA, **PRODUCTION)
    def test_it_is_not_a_menu_item(self) -> None:
        """A label, not a control.

        ``role="menuitem"`` on something that does nothing when a keyboard
        user lands on it is a dead stop in the menu's tab order. The row is
        plain text with a screen-reader-only "Version" prefix, so it is
        announced with the menu but never focused.
        """
        body = self._client().get("/").content.decode("utf-8")
        row = body[body.index("v24") - 400 : body.index("v24")]

        assert 'role="menuitem"' not in row
        assert "sr-only" in row

    @override_settings(APP_RELEASE="", APP_VERSION=SHA, **PRODUCTION)
    def test_the_row_is_omitted_without_a_release_number(self) -> None:
        """No number, no row — never an empty line under Settings."""
        body = self._client().get("/").content.decode("utf-8")

        assert "sw-update" in body  # sanity: the page rendered
        assert ">Version<" not in body
