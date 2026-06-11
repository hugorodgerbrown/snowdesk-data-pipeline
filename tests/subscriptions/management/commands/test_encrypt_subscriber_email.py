"""
tests/subscriptions/management/commands/test_encrypt_subscriber_email.py — Tests for
encrypt_subscriber_email management command.

Covers:
  - Dry-run (default): reports the count, writes nothing to the DB.
  - --commit: re-saves rows so the email column holds ciphertext.
  - Idempotency: running --commit twice produces identical ciphertext.
  - All rows processed: verified via raw SQL cursor.
"""

import pytest
from django.core.management import call_command
from django.db import connection

from subscriptions.fields import decrypt_value
from tests.factories import SubscriberFactory


@pytest.mark.django_db(transaction=True)
class TestEncryptSubscriberEmailDryRun:
    """Dry-run behaviour: count reported, no writes."""

    def test_dry_run_reports_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Dry-run prints how many rows would be processed."""
        SubscriberFactory.create(email="alice@example.com")
        SubscriberFactory.create(email="bob@example.com")
        call_command("encrypt_subscriber_email")
        out = capsys.readouterr().out
        assert "2" in out

    def test_dry_run_does_not_write(self) -> None:
        """Dry-run leaves the DB column unchanged.

        Because SubscriberFactory goes through the ORM which calls
        get_prep_value, newly-created rows are already ciphertext.
        The point is: the command itself must not change anything.
        """
        sub = SubscriberFactory.create(email="alice@example.com")
        # Read raw column value before dry-run.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            row = cursor.fetchone()
        before = row[0]

        call_command("encrypt_subscriber_email")

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            row = cursor.fetchone()
        after = row[0]

        assert before == after


@pytest.mark.django_db(transaction=True)
class TestEncryptSubscriberEmailCommit:
    """--commit behaviour: rows are (re-)encrypted in the DB."""

    def test_commit_writes_ciphertext_to_db(self) -> None:
        """After --commit the raw DB column holds ciphertext, not plaintext."""
        plaintext = "carol@example.com"
        sub = SubscriberFactory.create(email=plaintext)

        call_command("encrypt_subscriber_email", commit=True)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            row = cursor.fetchone()
        raw = row[0]

        assert raw != plaintext
        assert decrypt_value(raw) == plaintext

    def test_commit_is_idempotent(self) -> None:
        """Running --commit twice produces identical ciphertext (deterministic)."""
        sub = SubscriberFactory.create(email="dan@example.com")

        call_command("encrypt_subscriber_email", commit=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            first_raw = cursor.fetchone()[0]

        call_command("encrypt_subscriber_email", commit=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            second_raw = cursor.fetchone()[0]

        assert first_raw == second_raw
        assert decrypt_value(first_raw) == "dan@example.com"

    def test_commit_reports_success_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--commit reports how many rows were encrypted."""
        SubscriberFactory.create(email="eve@example.com")
        SubscriberFactory.create(email="frank@example.com")
        call_command("encrypt_subscriber_email", commit=True)
        out = capsys.readouterr().out
        assert "2" in out

    def test_commit_multiple_rows(self) -> None:
        """--commit processes all rows, not just the first one."""
        emails = ["g@example.com", "h@example.com", "i@example.com"]
        subs = [SubscriberFactory.create(email=e) for e in emails]

        call_command("encrypt_subscriber_email", commit=True)

        for sub, plaintext in zip(subs, emails, strict=True):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                    [sub.pk],
                )
                raw = cursor.fetchone()[0]
            assert raw != plaintext
            assert decrypt_value(raw) == plaintext


@pytest.mark.django_db(transaction=True)
class TestEncryptSubscriberEmailLegacyRows:
    """Verify the command encrypts legacy-plaintext rows correctly."""

    def test_commit_encrypts_legacy_plaintext_row(self) -> None:
        """A row written as plaintext (bypassing the ORM) is encrypted by --commit.

        We write a plaintext email directly via raw SQL to simulate a pre-migration
        row, then verify the command re-saves it as ciphertext.
        """
        sub = SubscriberFactory.create(email="legacy@example.com")

        # Overwrite the column with plaintext, bypassing the ORM.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE subscriptions_subscriber SET email = %s WHERE id = %s",
                ["legacy@example.com", sub.pk],
            )

        # Verify the overwrite worked.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            assert cursor.fetchone()[0] == "legacy@example.com"

        # Run the backfill.
        call_command("encrypt_subscriber_email", commit=True)

        # Now the column should hold ciphertext.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM subscriptions_subscriber WHERE id = %s",
                [sub.pk],
            )
            raw = cursor.fetchone()[0]

        assert raw != "legacy@example.com"
        assert decrypt_value(raw) == "legacy@example.com"
