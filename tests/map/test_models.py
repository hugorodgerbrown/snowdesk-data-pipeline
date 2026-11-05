"""tests/map/test_models.py — Map3dShare, the 3-D map's share token."""

from __future__ import annotations

import pytest

from apps.map.models import Map3dShare
from tests.factories import Map3dShareFactory

pytestmark = pytest.mark.django_db


def test_a_share_mints_a_url_safe_token() -> None:
    """The default token is 11 URL-safe characters, distinct per row."""
    a = Map3dShareFactory.create()
    b = Map3dShareFactory.create()
    assert len(a.token) == 11
    assert a.token != b.token


def test_for_token_finds_the_row_and_ignores_a_blank() -> None:
    """for_token matches exactly and never matches an empty token."""
    share = Map3dShareFactory.create()
    assert list(Map3dShare.objects.for_token(share.token)) == [share]
    assert not Map3dShare.objects.for_token("").exists()


def test_str_delegates_to_to_string() -> None:
    """__str__ is to_string()."""
    share = Map3dShareFactory.create()
    assert str(share) == share.to_string()
    assert share.token in str(share)
