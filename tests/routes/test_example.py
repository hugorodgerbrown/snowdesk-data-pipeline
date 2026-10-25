"""
tests/routes/test_example.py

Covers the example route (SNOW-1230):
  - RouteShareQuerySet.active() keeps the example past its expires_at;
    claimable() leaves it out; example() answers zero or one live row;
    is_live agrees with active() for the example too.
  - The partial unique constraint: two marked shares are refused.
  - apps.routes.services.example: example_owner (idempotent, unusable
    password), set_example (new row, unchanged no-op, same route new day,
    another route unmarks the old one onto an ordinary window).
  - claim_route_share refuses the example.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.routes.models import RouteShare
from apps.routes.services.example import (
    EXAMPLE_OWNER_EMAIL,
    example_owner,
    example_share,
    set_example,
)
from apps.routes.services.shares import claim_route_share
from tests.factories import RouteFactory, RouteShareFactory, UserFactory


@pytest.mark.django_db
class TestExampleQuerysets:
    """What the two flags change on the queryset, and nothing else."""

    def test_an_expired_example_is_still_active(self) -> None:
        """The example has no window."""
        share = RouteShareFactory.create(
            example=True, expires_at=timezone.now() - timedelta(days=1)
        )

        assert list(RouteShare.objects.active()) == [share]
        assert share.is_live

    def test_a_deleted_examples_route_is_not_active(self) -> None:
        """Deleting the route still stops the example."""
        share = RouteShareFactory.create(example=True)
        assert share.route is not None
        share.route.delete()
        share.refresh_from_db()

        assert not RouteShare.objects.active().exists()
        assert not share.is_live
        assert example_share() is None

    def test_the_example_is_never_claimable(self) -> None:
        """claimable() is active() less the example."""
        ordinary = RouteShareFactory.create()
        RouteShareFactory.create(example=True)

        assert list(RouteShare.objects.claimable()) == [ordinary]

    def test_example_returns_only_the_marked_share(self) -> None:
        """An ordinary share is not the example."""
        RouteShareFactory.create()
        share = RouteShareFactory.create(example=True)

        assert list(RouteShare.objects.example()) == [share]

    def test_two_examples_are_refused(self) -> None:
        """One example, ever."""
        RouteShareFactory.create(example=True)

        with pytest.raises(IntegrityError):
            RouteShareFactory.create(example=True)


@pytest.mark.django_db
class TestExampleOwner:
    """The account nobody signs in to."""

    def test_it_is_created_once(self) -> None:
        """A second call returns the same user."""
        first = example_owner()
        second = example_owner()

        assert first.pk == second.pk
        assert first.email == EXAMPLE_OWNER_EMAIL

    def test_it_has_no_usable_password(self) -> None:
        """Nobody signs in as it."""
        assert not example_owner().has_usable_password()


@pytest.mark.django_db
class TestSetExample:
    """set_example is the only writer of the flag."""

    def test_it_marks_a_new_share(self) -> None:
        """The first example is a new share of the route."""
        route = RouteFactory.create()

        share = set_example(route, date(2026, 4, 10))

        assert share.is_example
        assert share.example_date == date(2026, 4, 10)
        assert share.route_id == route.pk
        assert example_share() == share

    def test_an_unchanged_example_is_returned_as_it_is(self) -> None:
        """Same route, same day: nothing is written."""
        route = RouteFactory.create()
        share = set_example(route, date(2026, 4, 10))

        again = set_example(route, date(2026, 4, 10))

        assert again.pk == share.pk
        assert RouteShare.objects.count() == 1

    def test_a_new_day_moves_the_existing_row(self) -> None:
        """A link already handed out still names the example."""
        route = RouteFactory.create()
        share = set_example(route, date(2026, 4, 10))

        moved = set_example(route, date(2026, 3, 2))

        assert moved.pk == share.pk
        assert moved.example_date == date(2026, 3, 2)
        assert RouteShare.objects.count() == 1

    def test_another_route_unmarks_the_old_example(self) -> None:
        """The old row becomes an ordinary share with an ordinary window."""
        old = set_example(RouteFactory.create(), date(2026, 4, 10))
        new_route = RouteFactory.create()

        new = set_example(new_route, date(2026, 4, 10))
        old.refresh_from_db()

        assert new.pk != old.pk
        assert not old.is_example
        assert old.expires_at > timezone.now()
        assert example_share() == new


@pytest.mark.django_db
class TestClaimRefusesTheExample:
    """No Save writes the example onto anyone's account."""

    def test_claiming_the_example_raises_does_not_exist(self) -> None:
        """The same exception as an unknown token, on purpose."""
        share = RouteShareFactory.create(example=True)

        with pytest.raises(RouteShare.DoesNotExist):
            claim_route_share(UserFactory.create(), share.token)
