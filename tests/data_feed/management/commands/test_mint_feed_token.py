"""
tests/data_feed/management/commands/test_mint_feed_token.py — mint_feed_token (SNOW-1254).

The command's refusals, its read-only default, the token it mints, and the
end-to-end proof: a minted token opens the feed through the unchanged
``require_feed_token``, and stops opening it once expired.
"""

from __future__ import annotations

import datetime as dt
import re
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from tests.factories import OAuthApplicationFactory

URL = "/api/data/v1/locations/"


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("mint_feed_token", *args, stdout=out)
    return out.getvalue()


def _minted(output: str) -> str:
    """Pull the raw token out of the command's output."""
    match = re.search(r"\n\n(\S+)\n\n", output)
    assert match, output
    return match.group(1)


@pytest.fixture
def cloud_client(db: None) -> Application:
    """A service client for cloud dev environments."""
    return OAuthApplicationFactory.create(
        name="Cloud dev feed",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        redirect_uris="",
    )


@pytest.fixture
def testserver_site(settings: Any) -> None:
    """Make this site the test client's host, so the default binding matches."""
    settings.SITE_BASE_URL = "http://testserver"


@pytest.mark.django_db
@pytest.mark.usefixtures("testserver_site")
class TestMint:
    """What the command writes."""

    def test_a_dry_run_writes_nothing(self, cloud_client: Application) -> None:
        """Without --commit no token exists afterwards."""
        output = _run()
        assert "Read-only run" in output
        assert AccessToken.objects.count() == 0

    def test_commit_mints_a_bound_userless_data_token(
        self, cloud_client: Application
    ) -> None:
        """Scope data, no user, bound to the feed, expiring in --days."""
        _run("--days", "30", "--commit")
        token = AccessToken.objects.get()
        assert token.application == cloud_client
        assert token.user is None
        assert token.scope == "data"
        assert token.resource == ["http://testserver/api/data/v1/"]
        expected = timezone.now() + dt.timedelta(days=30)
        assert abs((token.expires - expected).total_seconds()) < 60

    def test_the_minted_token_opens_the_feed(
        self, client: Client, cloud_client: Application
    ) -> None:
        """require_feed_token admits it unchanged."""
        raw = _minted(_run("--commit"))
        response = client.get(URL, HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert response.status_code == 200

    def test_an_expired_minted_token_is_refused(
        self, client: Client, cloud_client: Application
    ) -> None:
        """Past its expiry the token is a 401."""
        raw = _minted(_run("--commit"))
        AccessToken.objects.update(expires=timezone.now() - dt.timedelta(seconds=1))
        response = client.get(URL, HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert response.status_code == 401


@pytest.mark.django_db
class TestRefusals:
    """The command refuses anything but a live service client."""

    def test_unknown_client(self) -> None:
        """No application by that name."""
        with pytest.raises(CommandError, match="No OAuth application"):
            _run("--client", "Nobody", "--commit")

    def test_not_a_service_client(self) -> None:
        """A registered (DCR) client-credentials app is not a service client."""
        OAuthApplicationFactory.create(
            name="Registered",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
            registration_source=Application.RegistrationSource.DCR,
        )
        with pytest.raises(CommandError, match="not a service client"):
            _run("--client", "Registered", "--commit")
        assert AccessToken.objects.count() == 0

    def test_days_must_be_positive(self, cloud_client: Application) -> None:
        """Zero days is refused."""
        with pytest.raises(CommandError, match="--days"):
            _run("--days", "0", "--commit")
