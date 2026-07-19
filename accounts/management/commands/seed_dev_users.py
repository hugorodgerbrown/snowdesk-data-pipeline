"""
accounts/management/commands/seed_dev_users.py — Dev-only user seed command.

Creates two local development accounts so that a fresh worktree DB (seeded from
``test_data`` fixtures) can be used for manual testing immediately:

- A Django superuser for the ``/admin/`` interface.
- An active, region-subscribed normal user for the full subscription journey.

Both accounts use a well-known shared password documented in ``docs/worktrees.md``.

Refuses to run when ``DEBUG`` is ``False`` so it cannot be accidentally invoked
in production.

Idempotent: safe to re-run; uses ``update_or_create`` / ``get_or_create``
throughout — no duplicate rows are created.

Usage::

    uv run python manage.py seed_dev_users
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Subscriber, Subscription
from regions.models import MicroRegion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dev-only credentials — documented in docs/worktrees.md
# ---------------------------------------------------------------------------

SUPERUSER_EMAIL = "admin@snowdesk.dev"
NORMAL_USER_EMAIL = "dev@snowdesk.dev"
PASSWORD = "snowdesk"  # noqa: S105 — intentional dev-only constant, documented in docs/worktrees.md

# CH-4115 is the canonical test region (Martigny-Verbier), loaded from the
# eaws_CH region fixture and seeded with bulletins by seed_test_data.
SUBSCRIBED_REGION_ID = "CH-4115"


class Command(BaseCommand):
    """Seed the local DB with two well-known dev accounts."""

    help = (
        "Create (or update) a superuser and a subscribed normal user for local "
        "development. Safe to re-run. Refuses to run when DEBUG is False."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Create or update the dev superuser and the subscribed normal user."""
        if not settings.DEBUG:
            raise CommandError("This command is only available when DEBUG=True.")

        verbosity = options["verbosity"]
        User = get_user_model()  # noqa: N806 — conventional upper-case alias

        # ------------------------------------------------------------------ #
        # Superuser                                                            #
        # ------------------------------------------------------------------ #

        email_super = SUPERUSER_EMAIL.lower()
        user_super, created_super = User.objects.update_or_create(
            username=email_super,
            defaults={
                "email": email_super,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        # PASSWORD is an intentional dev-only constant (DEBUG-gated above); the
        # unvalidated-password semgrep rule is excluded project-wide (see tox.ini).
        user_super.set_password(PASSWORD)
        user_super.save()

        if verbosity >= 1:
            action = "created" if created_super else "updated"
            self.stdout.write(f"Superuser {action}: {email_super}")

        # ------------------------------------------------------------------ #
        # Subscribed normal user                                               #
        # ------------------------------------------------------------------ #

        email_dev = NORMAL_USER_EMAIL.lower()
        subscriber, created_subscriber = Subscriber.objects.get_or_create_for_email(
            email_dev,
            defaults={
                "status": Subscriber.Status.ACTIVE,
                "confirmed_at": timezone.now(),
            },
        )

        # get_or_create_for_email leaves the User with an unusable password;
        # set it explicitly so the account is immediately usable for testing.
        subscriber.user.set_password(PASSWORD)
        subscriber.user.save()

        # Ensure the subscriber is active even if it pre-existed as pending.
        if not created_subscriber and subscriber.status != Subscriber.Status.ACTIVE:
            subscriber.status = Subscriber.Status.ACTIVE
            if subscriber.confirmed_at is None:
                subscriber.confirmed_at = timezone.now()
            subscriber.save(update_fields=["status", "confirmed_at", "updated_at"])

        if verbosity >= 1:
            action = "created" if created_subscriber else "updated"
            self.stdout.write(f"Subscriber {action}: {email_dev}")

        # ------------------------------------------------------------------ #
        # Subscription to CH-4115                                              #
        # ------------------------------------------------------------------ #

        try:
            region = MicroRegion.objects.get(region_id=SUBSCRIBED_REGION_ID)
        except MicroRegion.DoesNotExist:
            logger.error(
                "MicroRegion %s not found — run "
                "'uv run python manage.py loaddata eaws_CH resorts' first.",
                SUBSCRIBED_REGION_ID,
            )
            raise CommandError(
                f"MicroRegion {SUBSCRIBED_REGION_ID!r} does not exist. "
                "Load the region fixtures first: "
                "uv run python manage.py loaddata eaws_CH resorts"
            )
        _subscription, created_sub = Subscription.objects.get_or_create(
            subscriber=subscriber,
            region=region,
            defaults={"geo_match_kind": Subscription.GeoMatchKind.IN_REGION},
        )

        if verbosity >= 1:
            action = "created" if created_sub else "already exists"
            self.stdout.write(
                f"Subscription to {SUBSCRIBED_REGION_ID} {action}: {email_dev}"
            )
