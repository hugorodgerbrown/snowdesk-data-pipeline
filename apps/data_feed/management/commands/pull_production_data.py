r"""
apps/data_feed/management/commands/pull_production_data.py — pull_production_data.

Pulls what production has ingested — bulletins, weather, and the curated
resort estate with its locations — from production's data feed over HTTPS,
and applies it here (SNOW-1197). Staging is the first client: it has no
scheduler, so it never ingests a bulletin or a day of weather of its own.

**Idempotent.** Every item is matched on its natural key and compared with
the row already here; an identical row is not written. Running the same
pull twice writes nothing the second time, so it is safe to run whenever
staging looks stale. ``--type`` picks what to pull:

* ``resorts`` — the curated locations (and which regions they are the
  centroid of), then the resorts with their pins, passes and location
  links. Always the whole estate: a few thousand rows, and a pass or a
  location link can change without the resort's ``updated_at`` moving.
* ``bulletins`` — each changed bulletin's CAAML, replayed through this
  database's own ``upsert_bulletin``, which rebuilds its region links, day
  ratings, grouping outline and render model.
* ``weather`` — each changed location-day, through ``upsert_weather``. A
  past day already recorded here is kept, never rewritten.

With no ``--type``, all three, in that order: weather hangs off locations.

**What "changed" means.** Bulletins and weather are pulled from ``--since``
onwards. Without it, a type resumes from where the last successful
``--commit`` run that pulled it started, less an hour's overlap for clock
skew and rows committed mid-run (re-pulling the overlap is free — it is
idempotent). The first pull of a type goes back seven days; pass
``--since`` for a deeper backfill. ``bin/sync-staging-data`` remains the
way to reset staging wholesale.

Reads ``DATA_FEED_URL``, ``DATA_FEED_CLIENT_ID`` and
``DATA_FEED_CLIENT_SECRET`` (a service client created in production's
admin — see ``docs/data-feed.md``).

Usage:
    # Preview: reads the feed, compares, writes nothing.
    uv run python manage.py pull_production_data

    # Apply everything changed since the last pull.
    uv run python manage.py pull_production_data --commit

    # Just bulletins, back to the start of the season.
    uv run python manage.py pull_production_data --type bulletins \
        --since 2026-10-01 --commit
"""

from __future__ import annotations

import datetime as dt
import logging
from argparse import ArgumentParser, ArgumentTypeError
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.bulletins.models import PipelineRun
from apps.core.command_iteration import countdown
from apps.core.command_register import RegisteredCommand
from apps.core.models import CommandRun
from apps.data_feed.client import FeedClient, FeedError
from apps.data_feed.services.apply import (
    Outcome,
    PageLookups,
    apply_bulletin,
    apply_location,
    apply_resort,
    apply_weather,
    lookups_for,
)

logger = logging.getLogger(__name__)

COMMAND_NAME = "pull_production_data"

# What --type offers, and the feeds each one reads, in the order applied.
TYPES: dict[str, tuple[str, ...]] = {
    "resorts": ("locations", "resorts"),
    "bulletins": ("bulletins",),
    "weather": ("weather",),
}

# Feeds pulled whole every time, whatever --since says (see the docstring).
SNAPSHOT_FEEDS = frozenset({"locations", "resorts"})

# How far back the first pull of a type goes, and the overlap a resumed
# pull re-reads behind the last run's start.
FIRST_PULL_LOOKBACK = dt.timedelta(days=7)
RESUME_OVERLAP = dt.timedelta(hours=1)

# How many recent committed runs to search for a type's watermark.
WATERMARK_SEARCH_DEPTH = 50


def since_arg(raw: str) -> dt.datetime:
    """Argparse ``type=`` for ``--since``: a date or an aware datetime.

    A bare date means midnight UTC at its start.

    Args:
        raw: The command-line string.

    Returns:
        An aware datetime.

    Raises:
        ArgumentTypeError: It is neither, or a datetime with no offset.

    """
    # The date first: parse_datetime also accepts a bare date, as midnight
    # with no offset, which the offset rule below would then refuse.
    day = parse_date(raw)
    if day is not None:
        return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ArgumentTypeError(f"{raw!r} is not a date or ISO 8601 datetime")
    if parsed.tzinfo is None:
        raise ArgumentTypeError(f"{raw!r} has no UTC offset")
    return parsed


def watermark(type_name: str, *, now: dt.datetime) -> tuple[dt.datetime, str]:
    """Return where a pull of ``type_name`` resumes, and why.

    The start of the last successful ``--commit`` run whose ``--type``
    included it (or that named no type, meaning all), less
    ``RESUME_OVERLAP``; or ``FIRST_PULL_LOOKBACK`` before ``now`` when
    there is none.

    Args:
        type_name: A key of ``TYPES``.
        now: The current time.

    Returns:
        ``(since, reason)``.

    """
    runs = (
        CommandRun.objects.for_command(COMMAND_NAME)
        .committed()
        .filter(status=CommandRun.STATUS.SUCCEEDED)
        .order_by("-started_at")
        .only("started_at", "arguments")[:WATERMARK_SEARCH_DEPTH]
    )
    for run in runs:
        types = (run.arguments or {}).get("types") or list(TYPES)
        if type_name in types:
            return (
                run.started_at - RESUME_OVERLAP,
                f"last pull started {run.started_at:%Y-%m-%d %H:%M} UTC, less 1h",
            )
    return now - FIRST_PULL_LOOKBACK, "first pull of this type: the last 7 days"


def describe_item(feed: str) -> Callable[[dict[str, Any]], str]:
    """Return the countdown label for one item of ``feed``.

    Reads only the item, never the database.

    Args:
        feed: The feed's name.

    Returns:
        A function from item to label.

    """
    if feed == "locations":
        return lambda item: item["fields"].get("name") or item["uuid"]
    if feed == "resorts":
        return lambda item: str(item["slug"])
    if feed == "bulletins":

        def bulletin(item: dict[str, Any]) -> str:
            """Name a bulletin by id and the day it is valid from."""
            start = ((item.get("caaml") or {}).get("validTime") or {}).get(
                "startTime", ""
            )
            return f"{item['bulletin_id']} {start[:10]}"

        return bulletin
    return lambda item: f"{item['observed_on']} at {item['location']}"


def walk(
    client: FeedClient, feed: str, *, since: dt.datetime | None
) -> tuple[int, Iterator[list[dict[str, Any]]]]:
    """Start walking ``feed``: return its total and an iterator over its pages.

    The first page is fetched here, for the total it reports; the rest are
    fetched as the iterator reaches them, so a pull of thousands of items
    holds one page at a time. Pages, not items, so the command can read
    what each page refers to in one go (SNOW-1199).

    Args:
        client: The feed client.
        feed: The feed's name.
        since: Rows updated at or after this; None for the whole feed.

    Returns:
        ``(total, pages)``, each page a list of items.

    Raises:
        FeedError: The first page could not be read (later pages raise it
            from the iterator).

    """
    pages = client.pages(feed, since=since.isoformat() if since else None)
    first = next(pages)

    def results() -> Iterator[list[dict[str, Any]]]:
        """Yield every page's items, the first page's then the rest's."""
        yield first.results
        for later in pages:
            yield later.results

    total = first.count if first.count is not None else len(first.results)
    return total, results()


def with_lookups(
    feed: str, pages: Iterator[list[dict[str, Any]]], *, planned: set[str]
) -> Iterator[tuple[dict[str, Any], PageLookups | None]]:
    """Yield each item with its page's lookups, read once a page (SNOW-1199).

    A page whose lookups cannot be read is not allowed to stop the run: its
    items are yielded with None, and each reads its own lookups inside
    ``Command._apply``, where a failure is counted against that item alone.

    Args:
        feed: The feed's name.
        pages: The feed's pages, from ``walk``.
        planned: The run's locations a dry run would create (SNOW-1198).

    Yields:
        ``(item, lookups)``, lookups None when the page's could not be read.

    """
    for page in pages:
        lookups: PageLookups | None
        try:
            lookups = lookups_for(feed, page, planned=planned)
        except Exception:
            # Broad on purpose, as in _apply: a malformed item must cost
            # only itself, so the page falls back to per-item lookups.
            logger.exception(
                "%s: %s page lookups failed; reading per item", COMMAND_NAME, feed
            )
            lookups = None
        for item in page:
            yield item, lookups


class Command(RegisteredCommand):
    """Pull bulletins, weather and resorts from production's data feed.

    Read-only by default; pass --commit to write. Exits non-zero when any
    item failed or the feed could not be read.
    """

    help = (
        "Pull bulletins, weather and resorts (+ locations) from production's "
        "data feed over HTTPS and apply them idempotently. Read-only unless "
        "--commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--type",
            dest="types",
            action="append",
            choices=list(TYPES),
            help=(
                "What to pull: resorts (with their locations), bulletins or "
                "weather. Repeat for more than one. Default: all three."
            ),
        )
        parser.add_argument(
            "--since",
            type=since_arg,
            help=(
                "Pull bulletins and weather updated at or after this date or "
                "ISO 8601 datetime. Default: resume from the last successful "
                "--commit pull of that type (the last 7 days on the first)."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write. Without it the feed is read and compared, nothing written.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Pull each requested type in order and apply its items."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        types: list[str] = options["types"] or list(TYPES)
        client = self._client()
        now = timezone.now()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Pulling {', '.join(t for t in TYPES if t in types)} from "
                f"{client.base_url}{flag_label}"
            )
        )

        failed = 0
        # Locations a dry run would create, shared across this run's feeds
        # so the weather and resorts after them do not fail (SNOW-1198).
        planned: set[str] = set()
        for type_name in TYPES:
            if type_name not in types:
                continue
            since, reason = (
                (options["since"], "--since")
                if options["since"]
                else watermark(type_name, now=now)
            )
            for feed in TYPES[type_name]:
                failed += self._pull(
                    client,
                    feed,
                    since=None if feed in SNAPSHOT_FEEDS else since,
                    reason=reason,
                    commit=commit,
                    verbosity=verbosity,
                    planned=planned,
                )

        if failed:
            raise CommandError(
                f"{COMMAND_NAME} finished with {failed} failed item(s). "
                "Check the log for details."
            )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    "Done."
                    if commit
                    else "Read-only run complete. Pass --commit to write."
                )
            )

    def _client(self) -> FeedClient:
        """Build the feed client from settings, refusing a misconfiguration.

        Returns:
            The client.

        Raises:
            CommandError: A setting is missing, the URL is not HTTPS, or it
                points at this site.

        """
        missing = [
            name
            for name in (
                "DATA_FEED_URL",
                "DATA_FEED_CLIENT_ID",
                "DATA_FEED_CLIENT_SECRET",
            )
            if not getattr(settings, name, "")
        ]
        if missing:
            raise CommandError(
                f"{', '.join(missing)} not set — see docs/data-feed.md for "
                "creating a service client on production."
            )
        try:
            client = FeedClient(
                settings.DATA_FEED_URL,
                settings.DATA_FEED_CLIENT_ID,
                settings.DATA_FEED_CLIENT_SECRET,
            )
        except FeedError as exc:
            raise CommandError(str(exc)) from exc
        own_host = urlsplit(settings.SITE_BASE_URL).hostname
        if own_host and urlsplit(client.base_url).hostname == own_host:
            raise CommandError(
                f"DATA_FEED_URL points at this site ({own_host}); a site "
                "cannot pull from its own feed."
            )
        return client

    def _pull(
        self,
        client: FeedClient,
        feed: str,
        *,
        since: dt.datetime | None,
        reason: str,
        commit: bool,
        verbosity: int,
        planned: set[str],
    ) -> int:
        """Walk one feed and apply each item, tallying every outcome.

        Args:
            client: The feed client.
            feed: The feed's name.
            since: Pull rows updated at or after this; None for the whole feed.
            reason: Where ``since`` came from, for the operator.
            commit: Write; otherwise only decide.
            verbosity: Django's ``--verbosity``.
            planned: The run's locations a dry run would create.

        Returns:
            How many items failed.

        Raises:
            CommandError: The feed could not be read.

        """
        window = (
            "everything"
            if since is None
            else f"since {since:%Y-%m-%d %H:%M} UTC ({reason})"
        )
        self.stdout.write(self.style.MIGRATE_LABEL(f"{feed}: {window}"))
        counts = {outcome: 0 for outcome in Outcome}
        run = (
            PipelineRun.objects.create(triggered_by=COMMAND_NAME)
            if commit and feed == "bulletins"
            else None
        )
        if run is not None:
            run.mark_running()
        try:
            total, pages = walk(client, feed, since=since)
            describe = describe_item(feed)
            # SNOW-602 exempt: the unit of work is a feed item fetched over
            # HTTPS, not a row of a local queryset; countdown counts down
            # the total the feed's first page reports.
            for item, lookups in countdown(
                self,
                with_lookups(feed, pages, planned=planned),
                total=total,
                verbosity=verbosity,
                label=f"{feed} item(s)",
                describe=lambda pair: describe(pair[0]),
            ):
                outcome = self._apply(
                    feed,
                    item,
                    commit=commit,
                    run=run,
                    lookups=lookups,
                    planned=planned,
                )
                counts[outcome] += 1
        except FeedError as exc:
            if run is not None:
                run.mark_failed(exc)
            self._tally(feed, counts)
            raise CommandError(f"{feed}: {exc}") from exc

        if run is not None:
            run.mark_success(counts[Outcome.CREATED], counts[Outcome.UPDATED])
        self._tally(feed, counts)
        if verbosity >= 1:
            self.stdout.write(
                f"{feed}: "
                + ", ".join(f"{counts[o]} {o.value}" for o in Outcome)
                + ("" if commit else " (would be)")
            )
        logger.info(
            "%s %s: %s commit=%s",
            COMMAND_NAME,
            feed,
            {o.value: n for o, n in counts.items()},
            commit,
        )
        return counts[Outcome.FAILED]

    def _apply(
        self,
        feed: str,
        item: dict[str, Any],
        *,
        commit: bool,
        run: PipelineRun | None,
        lookups: PageLookups | None,
        planned: set[str],
    ) -> Outcome:
        """Apply one item, turning any failure into ``Outcome.FAILED``.

        One bad item must not stop a pull of thousands, so every exception
        is caught, logged with its traceback and counted; a failed count
        above zero makes the command exit non-zero.

        Args:
            feed: The feed's name.
            item: The item.
            commit: Write; otherwise only decide.
            run: The bulletins ``PipelineRun``, when committing bulletins.
            lookups: The item's page's lookups; None to read the item's own.
            planned: The run's locations a dry run would create.

        Returns:
            The outcome.

        """
        try:
            lookups = lookups or lookups_for(feed, [item], planned=planned)
            if feed == "locations":
                return apply_location(item, commit=commit, lookups=lookups)
            if feed == "resorts":
                return apply_resort(item, commit=commit, lookups=lookups)
            if feed == "bulletins":
                return apply_bulletin(item, commit=commit, run=run, lookups=lookups)
            return apply_weather(item, commit=commit, lookups=lookups)
        except Exception:
            # Broad on purpose: a failure is any exception the owning write
            # path raises (upsert_bulletin's region checks, a constraint, a
            # malformed item), and each is reported per item, not fatal.
            logger.exception(
                "%s: %s item failed: %s",
                COMMAND_NAME,
                feed,
                item.get("uuid")
                or item.get("slug")
                or item.get("bulletin_id")
                or item.get("observed_on"),
            )
            return Outcome.FAILED

    def _tally(self, feed: str, counts: dict[Outcome, int]) -> None:
        """Record every outcome for ``feed`` in the run register, zeros included.

        ``failed`` is tallied under its bare name too, which is what marks
        the run PARTIAL in the register.

        Args:
            feed: The feed's name.
            counts: The outcome counts.

        """
        for outcome, n in counts.items():
            self.tally(f"{feed}_{outcome.value}", n)
        self.tally("failed", counts[Outcome.FAILED])
