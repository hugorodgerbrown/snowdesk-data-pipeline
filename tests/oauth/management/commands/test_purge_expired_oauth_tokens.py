"""
tests/oauth/management/commands/test_purge_expired_oauth_tokens.py (SNOW-1193).

Covers the read-only default, the committed purge through django-oauth-
toolkit's ``clear_expired()`` (expired access tokens go, live ones stay)
and the tallies the run leaves in the command run register.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from oauth2_provider.models import AccessToken

from apps.core.models import CommandRun
from tests.factories import OAuthAccessTokenFactory

pytestmark = pytest.mark.django_db


def _expired_and_live() -> tuple[AccessToken, AccessToken]:
    """Create one long-expired access token and one live one."""
    expired = OAuthAccessTokenFactory.create(expires=timezone.now() - timedelta(days=8))
    return expired, OAuthAccessTokenFactory.create()


def test_dry_run_deletes_nothing() -> None:
    """Without --commit it reports the row counts and writes nothing."""
    _expired_and_live()
    out = StringIO()
    call_command("purge_expired_oauth_tokens", stdout=out)
    assert "Read-only run" in out.getvalue()
    assert "access_tokens=2" in out.getvalue()
    assert AccessToken.objects.count() == 2


def test_commit_deletes_expired_tokens_and_keeps_live_ones() -> None:
    """--commit clears the expired token and tallies what went."""
    expired, live = _expired_and_live()
    out = StringIO()
    call_command("purge_expired_oauth_tokens", "--commit", stdout=out)
    assert list(AccessToken.objects.all()) == [live]
    assert "Deleted 1 access_tokens, 0 refresh_tokens, 0 codes." in out.getvalue()
    run = CommandRun.objects.get(command="purge_expired_oauth_tokens")
    assert run.counts == {
        "access_tokens_deleted": 1,
        "refresh_tokens_deleted": 0,
        "codes_deleted": 0,
    }


def test_quiet_commit_prints_nothing() -> None:
    """At verbosity 0 the run says nothing."""
    out = StringIO()
    call_command("purge_expired_oauth_tokens", "--commit", verbosity=0, stdout=out)
    assert out.getvalue() == ""
