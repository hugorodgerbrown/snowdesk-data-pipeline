"""
tests/accounts/management/commands/test_seed_dev_users.py — Tests for the
seed_dev_users management command.

Covers:
  - Superuser is created with is_staff, is_superuser, and a usable password.
  - Normal user is created with a Subscriber profile in ACTIVE status.
  - A Subscription to CH-4115 is created with geo_match_kind=IN_REGION.
  - Email addresses are stored in lowercase.
  - Idempotency: running the command twice produces no duplicate rows and the
    unique_together constraint on Subscription holds.
  - Production refusal: when DEBUG=False the command raises CommandError.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from accounts.management.commands.seed_dev_users import (
    NORMAL_USER_EMAIL,
    PASSWORD,
    SUBSCRIBED_REGION_ID,
    SUPERUSER_EMAIL,
)
from accounts.models import Subscriber, Subscription
from regions.models import MicroRegion
from tests.factories import MicroRegionFactory

User = get_user_model()


@pytest.fixture()
def ch4115_region(db: None) -> MicroRegion:
    """Create the CH-4115 MicroRegion used by seed_dev_users."""
    return MicroRegionFactory.create(region_id=SUBSCRIBED_REGION_ID)


@pytest.mark.django_db
class TestSeedDevUsersSuperuser:
    """Superuser creation tests."""

    def test_superuser_created(self, ch4115_region: MicroRegion) -> None:
        """seed_dev_users creates a superuser with is_staff and is_superuser."""
        call_command("seed_dev_users", verbosity=0)
        user = User.objects.get(username=SUPERUSER_EMAIL.lower())
        assert user.is_staff
        assert user.is_superuser

    def test_superuser_email_lowercased(self, ch4115_region: MicroRegion) -> None:
        """The superuser email is stored in lowercase."""
        call_command("seed_dev_users", verbosity=0)
        user = User.objects.get(username=SUPERUSER_EMAIL.lower())
        assert user.email == SUPERUSER_EMAIL.lower()

    def test_superuser_has_usable_password(self, ch4115_region: MicroRegion) -> None:
        """The superuser can authenticate with the dev password."""
        call_command("seed_dev_users", verbosity=0)
        user = User.objects.get(username=SUPERUSER_EMAIL.lower())
        assert user.check_password(PASSWORD)


@pytest.mark.django_db
class TestSeedDevUsersNormalUser:
    """Normal (subscribed) user creation tests."""

    def test_normal_user_has_subscriber_profile(
        self, ch4115_region: MicroRegion
    ) -> None:
        """seed_dev_users creates a Subscriber profile for the normal user."""
        call_command("seed_dev_users", verbosity=0)
        subscriber = Subscriber.objects.get(user__email=NORMAL_USER_EMAIL.lower())
        assert subscriber.status == Subscriber.Status.ACTIVE

    def test_normal_user_email_lowercased(self, ch4115_region: MicroRegion) -> None:
        """The normal user email is stored in lowercase."""
        call_command("seed_dev_users", verbosity=0)
        user = User.objects.get(username=NORMAL_USER_EMAIL.lower())
        assert user.email == NORMAL_USER_EMAIL.lower()

    def test_normal_user_has_usable_password(self, ch4115_region: MicroRegion) -> None:
        """The normal user can authenticate with the dev password."""
        call_command("seed_dev_users", verbosity=0)
        user = User.objects.get(username=NORMAL_USER_EMAIL.lower())
        assert user.check_password(PASSWORD)

    def test_normal_user_subscription_to_ch4115(
        self, ch4115_region: MicroRegion
    ) -> None:
        """The normal user has a Subscription to CH-4115 with geo_match_kind IN_REGION."""
        call_command("seed_dev_users", verbosity=0)
        subscriber = Subscriber.objects.get(user__email=NORMAL_USER_EMAIL.lower())
        sub = Subscription.objects.get(
            subscriber=subscriber, region__region_id=SUBSCRIBED_REGION_ID
        )
        assert sub.geo_match_kind == Subscription.GeoMatchKind.IN_REGION


@pytest.mark.django_db
class TestSeedDevUsersIdempotency:
    """Idempotency tests — running the command twice must not create duplicates."""

    def test_running_twice_no_duplicate_users(self, ch4115_region: MicroRegion) -> None:
        """Running seed_dev_users twice does not create duplicate User rows."""
        call_command("seed_dev_users", verbosity=0)
        call_command("seed_dev_users", verbosity=0)
        assert User.objects.filter(username=SUPERUSER_EMAIL.lower()).count() == 1
        assert User.objects.filter(username=NORMAL_USER_EMAIL.lower()).count() == 1

    def test_running_twice_no_duplicate_subscriptions(
        self, ch4115_region: MicroRegion
    ) -> None:
        """Running seed_dev_users twice does not violate Subscription unique_together."""
        call_command("seed_dev_users", verbosity=0)
        # A second run must not raise IntegrityError from (subscriber, region)
        call_command("seed_dev_users", verbosity=0)
        subscriber = Subscriber.objects.get(user__email=NORMAL_USER_EMAIL.lower())
        assert (
            Subscription.objects.filter(
                subscriber=subscriber, region__region_id=SUBSCRIBED_REGION_ID
            ).count()
            == 1
        )

    def test_password_still_valid_after_second_run(
        self, ch4115_region: MicroRegion
    ) -> None:
        """After a second run the password remains usable."""
        call_command("seed_dev_users", verbosity=0)
        call_command("seed_dev_users", verbosity=0)
        for email in (SUPERUSER_EMAIL.lower(), NORMAL_USER_EMAIL.lower()):
            user = User.objects.get(username=email)
            assert user.check_password(PASSWORD), f"Password invalid for {email}"


@pytest.mark.django_db
class TestSeedDevUsersProductionRefusal:
    """Production-guard tests."""

    @override_settings(DEBUG=False)
    def test_raises_command_error_when_debug_false(
        self, ch4115_region: MicroRegion
    ) -> None:
        """seed_dev_users raises CommandError when DEBUG=False."""
        with pytest.raises(CommandError):
            call_command("seed_dev_users", verbosity=0)

    def test_does_not_raise_when_debug_true(self, ch4115_region: MicroRegion) -> None:
        """seed_dev_users runs without error when DEBUG=True (the default in tests)."""
        # Verify DEBUG is True in the test environment so this test is meaningful.
        from django.conf import settings

        assert settings.DEBUG
        call_command("seed_dev_users", verbosity=0)
