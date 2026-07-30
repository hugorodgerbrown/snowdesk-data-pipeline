"""
tests/bulletins/test_bulletin_share_click_model.py — Tests for BulletinShareClick model.

Covers factory creation, to_string(), Meta.ordering, and CASCADE behaviour
when the parent BulletinShare is deleted. Request context (IP, UA, country, etc.)
is now accessed via click.request (a RequestLog FK), not inline fields.
"""

from __future__ import annotations

import pytest

from apps.bulletins.models import BulletinShare, BulletinShareClick
from tests.factories import (
    BulletinShareClickFactory,
    BulletinShareFactory,
    RequestLogFactory,
)


@pytest.mark.django_db
class TestBulletinShareClickFactory:
    """BulletinShareClickFactory produces valid instances."""

    def test_create_returns_instance(self) -> None:
        """Factory.create() returns a persisted BulletinShareClick."""
        click = BulletinShareClickFactory.create()
        assert isinstance(click, BulletinShareClick)
        assert click.pk is not None

    def test_share_fk_is_populated(self) -> None:
        """Factory wires the share FK by default."""
        click = BulletinShareClickFactory.create()
        assert click.share_id is not None

    def test_request_fk_is_populated(self) -> None:
        """Factory wires the request FK by default."""
        click = BulletinShareClickFactory.create()
        assert click.request_id is not None

    def test_request_country_code_via_fk(self) -> None:
        """Country code is accessed via click.request.country_code."""
        req_log = RequestLogFactory.create(country_code="CH")
        click = BulletinShareClickFactory.create(request=req_log)
        assert click.request.country_code == "CH"


@pytest.mark.django_db
class TestBulletinShareClickToString:
    """to_string() and __str__ return the expected format."""

    def test_to_string_includes_token_and_country(self) -> None:
        """to_string() embeds the share token and country_code from request."""
        share = BulletinShareFactory.create(token="xyz999")
        req_log = RequestLogFactory.create(country_code="CH")
        click = BulletinShareClickFactory.create(share=share, request=req_log)
        result = click.to_string()
        assert "xyz999" in result
        assert "CH" in result

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same as to_string()."""
        click = BulletinShareClickFactory.create()
        assert str(click) == click.to_string()


@pytest.mark.django_db
class TestBulletinShareClickOrdering:
    """BulletinShareClick.objects.all() is ordered by -created_at."""

    def test_ordering_newest_first(self) -> None:
        """Queryset ordering is -created_at."""
        share = BulletinShareFactory.create()
        c1 = BulletinShareClickFactory.create(share=share)
        c2 = BulletinShareClickFactory.create(share=share)
        ids = list(BulletinShareClick.objects.values_list("pk", flat=True))
        assert ids.index(c2.pk) < ids.index(c1.pk)


@pytest.mark.django_db
class TestBulletinShareClickCascade:
    """Deleting a BulletinShare cascades to its BulletinShareClick rows."""

    def test_click_deleted_when_share_deleted(self) -> None:
        """BulletinShareClick rows are CASCADE-deleted with their share."""
        share = BulletinShareFactory.create()
        click = BulletinShareClickFactory.create(share=share)
        click_pk = click.pk

        BulletinShare.objects.filter(pk=share.pk).delete()

        assert not BulletinShareClick.objects.filter(pk=click_pk).exists()
