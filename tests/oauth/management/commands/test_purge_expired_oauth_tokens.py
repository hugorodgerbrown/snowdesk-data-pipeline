"""
tests/oauth/management/commands/test_purge_expired_oauth_tokens.py (SNOW-1035).

Covers the dry run, the committed delete of long-dead tokens and codes, the
rows it must keep (live, recently dead, rotated-but-unexpired), the
countdown output and the non-zero exit on a failed delete.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.utils import timezone

from apps.oauth.models import AuthorizationCode, OAuthToken
from tests.factories import AuthorizationCodeFactory, OAuthTokenFactory

pytestmark = pytest.mark.django_db


def _dead_and_live() -> dict[str, Any]:
    """Create one row of each kind the command must delete or keep."""
    now = timezone.now()
    return {
        "expired": OAuthTokenFactory.create(expires_at=now - timedelta(days=8)),
        "revoked": OAuthTokenFactory.create(revoked_at=now - timedelta(days=8)),
        "recent": OAuthTokenFactory.create(revoked_at=now - timedelta(days=1)),
        "live": OAuthTokenFactory.create(),
        "rotated": OAuthTokenFactory.create(
            kind=OAuthToken.KIND.REFRESH,
            expires_at=now + timedelta(days=20),
            replaced_by=OAuthTokenFactory.create(kind=OAuthToken.KIND.REFRESH),
        ),
        "old_code": AuthorizationCodeFactory.create(expires_at=now - timedelta(days=8)),
        "new_code": AuthorizationCodeFactory.create(),
    }


def test_dry_run_deletes_nothing() -> None:
    """Without --commit it reports and writes nothing."""
    _dead_and_live()
    out = StringIO()
    call_command("purge_expired_oauth_tokens", stdout=out)
    assert "Would delete 2 token(s) and 1 code(s)" in out.getvalue()
    assert OAuthToken.objects.count() == 6
    assert AuthorizationCode.objects.count() == 2


def test_commit_deletes_only_long_dead_rows() -> None:
    """--commit removes the dead rows and keeps the rest."""
    rows = _dead_and_live()
    out = StringIO()
    call_command("purge_expired_oauth_tokens", "--commit", stdout=out)
    remaining = set(OAuthToken.objects.values_list("pk", flat=True))
    assert rows["expired"].pk not in remaining
    assert rows["revoked"].pk not in remaining
    for keep in ("recent", "live", "rotated"):
        assert rows[keep].pk in remaining
    assert list(AuthorizationCode.objects.all()) == [rows["new_code"]]
    output = out.getvalue()
    assert "token " in output
    assert "Deleted 2 token(s) and 1 code(s)." in output


def test_failed_delete_exits_non_zero() -> None:
    """A row that fails to delete makes the run fail."""
    OAuthTokenFactory.create(expires_at=timezone.now() - timedelta(days=8))
    with (
        mock.patch.object(OAuthToken, "delete", side_effect=DatabaseError("boom")),
        pytest.raises(CommandError, match="1 token"),
    ):
        call_command("purge_expired_oauth_tokens", "--commit", stdout=StringIO())
