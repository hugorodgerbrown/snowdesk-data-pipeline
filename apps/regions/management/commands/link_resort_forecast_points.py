"""
link_resort_forecast_points — anchor geocoded Resorts to a shared ForecastPoint.

One-shot backfill that anchors every geocoded ``Resort`` with no
``forecast_point`` yet to a shared ``bulletins.ForecastPoint`` (SNOW-503),
reusing the SNOW-416 machinery
(``apps.bulletins.services.forecast_points.resolve_forecast_point``). Widening
``ForecastPoint.objects.active()`` to favourite-OR-resort (see
``apps/bulletins/models.py``) means the scheduled ``fetch_weather`` point pass
picks up every linked resort automatically — no scheduler change needed.

For each candidate resort, ``resolve_forecast_point`` performs its own
Open-Meteo elevation lookup and reuses (or creates) the nearest matching
``ForecastPoint``; that external call is always made — even in a dry-run —
so the reported outcome reflects reality, but the resulting FK is only
written under ``--commit``. The lookup is kept outside any DB transaction
(mirroring ``apps.favourites.services.create_favourite``) so a slow or failing
request never holds a lock.

A resort that fails to resolve (e.g. the elevation lookup errors) is
logged and counted under ``failed``; it never aborts the rest of the
batch. The command exits non-zero when any resort failed, so cron/CI can
detect a partial run.

Safe-by-default (CLAUDE.md Option A): read-only unless ``--commit`` is
passed. Idempotent — a resort with ``forecast_point`` already set is
excluded from the candidate queryset, so a second run with nothing new to
geocode selects zero rows.

Usage:
    # Preview what would be linked — no writes, no --commit required to
    # exercise the resolve/reuse logic (only the FK write is gated).
    uv run python manage.py link_resort_forecast_points

    # Persist the resolved links.
    uv run python manage.py link_resort_forecast_points --commit

    # Tighten pacing between the per-resort elevation calls (default 1.0s).
    uv run python manage.py link_resort_forecast_points --commit --delay 2
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser, ArgumentTypeError
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.services.forecast_points import resolve_forecast_point
from apps.regions.models import Resort

logger = logging.getLogger(__name__)


def _non_negative_float(raw: str) -> float:
    """
    Argparse ``type=`` helper for non-negative float arguments.

    Args:
        raw: The raw command-line string.

    Returns:
        The parsed, non-negative float.

    Raises:
        ArgumentTypeError: if the value is unparseable or negative.

    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise ArgumentTypeError(f"invalid float value: {raw!r}") from exc
    if value < 0:
        raise ArgumentTypeError(f"delay must be non-negative (got {value})")
    return value


class Command(BaseCommand):
    """Link every geocoded, unlinked Resort to a shared ForecastPoint.

    Read-only by default; pass --commit to persist the FK. Resorts that
    are not geocoded (missing latitude/longitude) or already linked are
    excluded from the candidate set. Per-resort failures are caught,
    logged, and counted — they never abort the batch — and the command
    exits non-zero when any resort failed to resolve.
    """

    help = (
        "Resolve every geocoded, unlinked Resort to a shared ForecastPoint "
        "via apps.bulletins.services.forecast_points.resolve_forecast_point, "
        "widening the point-weather polling set to cover resorts. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the resolved forecast_point FK on each resort. "
                "Without this flag the command resolves (and reports) but "
                "writes nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=_non_negative_float,
            default=1.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive per-resort "
                "elevation lookups. Default 1.0 — paces the run inside "
                "Open-Meteo's free-tier rate limit. Pass 0 to disable "
                "pacing for a tiny resort count."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve every candidate resort and report the outcome."""
        commit: bool = options["commit"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        candidates = list(Resort.objects.geocoded().filter(forecast_point__isnull=True))

        self._announce(len(candidates), commit=commit, delay=delay)

        counts = _link_resorts(
            candidates,
            commit=commit,
            delay=delay,
            verbosity=verbosity,
        )

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"link_resort_forecast_points completed with "
                f"{counts['failed']} resort failure(s). Check logs for details."
            )

    def _announce(self, candidate_count: int, *, commit: bool, delay: float) -> None:
        """Write the start-of-run banner and matching log line."""
        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Linking {candidate_count} geocoded resort(s) to a "
                f"ForecastPoint{flag_label}"
            )
        )
        logger.info(
            "link_resort_forecast_points started: candidates=%d commit=%s delay=%s",
            candidate_count,
            commit,
            delay,
        )

    def _report_outcome(
        self,
        counts: dict[str, int],
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['linked']} linked, "
                        f"{counts['skipped']} skipped, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['linked']} resort(s) "
                        f"would be linked, {counts['failed']} failed to resolve. "
                        "No data written. Pass --commit to persist."
                    )
                )

        logger.info(
            "link_resort_forecast_points finished: linked=%d skipped=%d "
            "failed=%d commit=%s",
            counts["linked"],
            counts["skipped"],
            counts["failed"],
            commit,
        )


# ---------------------------------------------------------------------------
# Core linking logic
# ---------------------------------------------------------------------------


def _link_resorts(
    candidates: list[Resort],
    *,
    commit: bool,
    delay: float,
    verbosity: int,
) -> dict[str, int]:
    """
    Resolve a ForecastPoint for each candidate resort, optionally persisting it.

    Iterates the candidates in order, resolving each via
    ``resolve_forecast_point`` (an Open-Meteo elevation lookup kept outside
    any transaction). A per-resort failure is caught, logged, and counted
    under ``failed`` — it never aborts the rest of the batch. Under
    ``--commit`` a successfully resolved resort has its ``forecast_point``
    FK set and saved; otherwise nothing is written.

    Args:
        candidates: Geocoded, unlinked resorts to process.
        commit: Whether to persist the resolved FK.
        delay: Seconds to sleep between resorts (paces the elevation calls).
        verbosity: Django's ``--verbosity`` level.

    Returns:
        Counts dict with ``linked``, ``skipped``, and ``failed`` keys.
        ``skipped`` is always 0 here — the candidate queryset has already
        excluded ungeocoded and already-linked resorts — but the key is
        kept for symmetry with the other management commands' counts
        contract.

    """
    counts = {"linked": 0, "skipped": 0, "failed": 0}

    for index, resort in enumerate(candidates):
        # The candidate queryset (Resort.objects.geocoded()) already
        # guarantees non-null coordinates; the asserts only narrow the
        # type for mypy.
        assert resort.latitude is not None  # noqa: S101 — type narrowing, not a runtime guard
        assert resort.longitude is not None  # noqa: S101 — type narrowing, not a runtime guard
        try:
            # External HTTP call (Open-Meteo elevation lookup) — kept
            # outside any transaction so a slow or failing request never
            # holds a DB lock, mirroring create_favourite.
            forecast_point = resolve_forecast_point(resort.latitude, resort.longitude)
        except Exception:  # noqa: BLE001 — broad catch intentional: per-resort failure must not abort the batch
            logger.exception(
                "link_resort_forecast_points: failed to resolve resort %r (id=%s)",
                resort.name,
                resort.pk,
            )
            counts["failed"] += 1
        else:
            if commit:
                resort.forecast_point = forecast_point
                resort.save(update_fields=["forecast_point", "updated_at"])
            counts["linked"] += 1
            if verbosity >= 2:
                logger.info(
                    "Resolved resort %r (id=%s) -> ForecastPoint id=%s",
                    resort.name,
                    resort.pk,
                    forecast_point.pk,
                )

        if delay > 0 and index < len(candidates) - 1:
            time.sleep(delay)

    return counts
