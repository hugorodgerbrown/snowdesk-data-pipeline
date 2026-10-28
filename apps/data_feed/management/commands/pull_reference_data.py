r"""
apps/data_feed/management/commands/pull_reference_data.py — pull_reference_data.

Seeds or refreshes this instance from the reference-data service's v2 feeds
(``/api/data/v2/``, SNOW-1272): everything the anonymous map shows, read
from the one service that holds it
(``docs/decisions/reference-data-has-one-source-of-truth.md``). The sync is
one-way: nothing here is ever sent back.

**What is pulled, in order.** The regions are code-held fixtures every
instance loads with its migrations, so the pull starts after them:

1. ``locations``, ``passes``, ``resorts`` — the reference estate, always
   whole: a few thousand rows, matched on ``source_uuid``. A tombstone
   (``deleted_at``) removes the row once every live item is in, or — when
   this instance's user data points at it (a favourite, a trip, a field
   report) — detaches it from the estate and leaves the user's row its
   target (``apps/data_feed/services/reference.py``).
2. ``bulletins`` — each bulletin's CAAML with the service's render model,
   replayed through ``upsert_bulletin``.
3. ``day-ratings`` — the service's ``SourceDayRating``, copied over
   ``RegionDayRating``.
4. ``weather`` — each place's days, through ``upsert_weather``; a past day
   already recorded here is kept.

The time series are a subset, by the day each row is about: ``--recent N``
(the default, 14) pulls from N days ago onwards, forecasts included;
``--golden-week`` pulls the seven days ``seed_test_week`` covers, for
deterministic user testing. Every apply is idempotent, so a second run
writes nothing.

Staging runs it daily as the ``snowdesk-staging-data-pull`` cron
(``render.yaml``, SNOW-1281); ``pull_production_data``, the v1 client it
replaced there, stays for v1.

Reads ``DATA_FEED_URL`` (its ``/v1/`` read as ``/v2/``) and either
``DATA_FEED_V2_TOKEN`` (minted on the service with ``mint_feed_token
--resource https://<host>/api/data/v2/``) or the
``DATA_FEED_CLIENT_ID`` / ``DATA_FEED_CLIENT_SECRET`` pair.

Usage:
    # Preview: reads the feeds, compares, writes nothing.
    uv run python manage.py pull_reference_data

    # Seed: the estate, and the last fortnight of bulletins and weather.
    uv run python manage.py pull_reference_data --commit

    # The golden week instead.
    uv run python manage.py pull_reference_data --golden-week --commit
"""

from __future__ import annotations

import datetime as dt
import logging
from argparse import ArgumentParser
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from apps.bulletins.models import PipelineRun
from apps.bulletins.services.golden_week import golden_week_dates
from apps.core.command_iteration import countdown
from apps.core.command_register import RegisteredCommand
from apps.data_feed.client import FeedClient, FeedError, v2_url
from apps.data_feed.services.apply import apply_bulletin, apply_weather, lookups_for
from apps.data_feed.services.reference import (
    ABSENT,
    CREATED,
    DELETED,
    DETACHED,
    UNCHANGED,
    UPDATED,
    ReferenceIndex,
    SlugTaken,
    apply_reference_day_rating,
    apply_reference_location,
    apply_reference_pass,
    apply_reference_resort,
    apply_tombstone,
    reference_weather_lookups,
)

logger = logging.getLogger(__name__)

COMMAND_NAME = "pull_reference_data"

# The estate, pulled whole, in the order a resort's references need.
REFERENCE_FEEDS: tuple[str, ...] = ("locations", "passes", "resorts")
# Tombstones are applied after every live item, resorts first, so a resort
# has let go of a place before the place is removed.
TOMBSTONE_ORDER: tuple[str, ...] = ("resorts", "passes", "locations")
# Each estate feed's live-item apply.
REFERENCE_APPLY: dict[str, Callable[..., str]] = {
    "locations": apply_reference_location,
    "passes": apply_reference_pass,
    "resorts": apply_reference_resort,
}
# The time series, pulled for a window of days.
SERIES_FEEDS: tuple[str, ...] = ("bulletins", "day-ratings", "weather")

# Every outcome each feed can tally, so the register records zeros too.
FAILED = "failed"
KEPT = "kept"
RECOMPUTED = "recomputed"
SKIPPED = "skipped"
# Not an outcome: an item set aside to retry after the tombstones.
DEFERRED = "deferred"
OUTCOMES: dict[str, tuple[str, ...]] = {
    "locations": (CREATED, UPDATED, UNCHANGED, DELETED, DETACHED, ABSENT, FAILED),
    "passes": (CREATED, UPDATED, UNCHANGED, DELETED, ABSENT, FAILED),
    "resorts": (CREATED, UPDATED, UNCHANGED, DELETED, DETACHED, ABSENT, FAILED),
    "bulletins": (CREATED, UPDATED, UNCHANGED, FAILED),
    "day-ratings": (CREATED, UPDATED, UNCHANGED, RECOMPUTED, SKIPPED, FAILED),
    "weather": (CREATED, UPDATED, UNCHANGED, KEPT, SKIPPED, FAILED),
}

DEFAULT_RECENT_DAYS = 14


def place_label(item: dict[str, Any]) -> str:
    """Return what an operator reads for one v2 place.

    Its curated name; an unnamed region centre by the regions it is the
    centre of; anything else by its service uuid.

    Args:
        item: A v2 ``locations`` item, tombstone or live.

    Returns:
        The label.

    """
    name = (item.get("fields") or {}).get("name")
    if name:
        return str(name)
    centroid_of = (item.get("document") or {}).get("centroid_of")
    if centroid_of:
        return "centre of " + ", ".join(sorted(centroid_of))
    return str(item["uuid"])


def describe_item(
    feed: str, places: dict[str, str] | None = None
) -> Callable[[dict[str, Any]], str]:
    """Return the countdown label for one item of ``feed``.

    Reads only the item and ``places``, never the database.

    Args:
        feed: The feed's name.
        places: Each place's label by service uuid, from this run's
            ``locations`` feed, so a day of weather names its place.

    Returns:
        A function from item to label.

    """
    if feed == "locations":
        return place_label
    if feed in ("passes", "resorts"):
        return lambda item: str(item.get("slug") or item["uuid"])
    if feed == "bulletins":
        return lambda item: f"{item['bulletin_id']} {item.get('target_date') or ''}"
    if feed == "day-ratings":
        return lambda item: f"{item['region_code']} {item['date']}"
    names = places or {}
    return lambda item: (
        f"{item['observed_on']} at "
        f"{names.get(str(item['location'])) or item['location']}"
    )


def item_key(item: dict[str, Any]) -> object:
    """Return what names a failed item in the log."""
    return (
        item.get("uuid")
        or item.get("slug")
        or item.get("bulletin_id")
        or (item.get("region_code"), item.get("date"))
    )


class Command(RegisteredCommand):
    """Pull the reference estate and a window of its time series from v2.

    Read-only by default; pass --commit to write. Exits non-zero when any
    item failed or a feed could not be read.
    """

    help = (
        "Pull the reference-data service's places, passes and resorts "
        "(whole, tombstones honoured) and a window of its bulletins, day "
        "ratings and weather from /api/data/v2/. Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        window = parser.add_mutually_exclusive_group()
        window.add_argument(
            "--recent",
            type=int,
            default=DEFAULT_RECENT_DAYS,
            metavar="DAYS",
            help=(
                "Pull bulletins, day ratings and weather from this many days "
                f"ago onwards, forecasts included. Default: {DEFAULT_RECENT_DAYS}."
            ),
        )
        window.add_argument(
            "--golden-week",
            action="store_true",
            help="Pull the golden week's days instead (seed_test_week's).",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write. Without it the feeds are read and compared, nothing written.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Pull the estate, apply its tombstones, then the time series."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        if options["recent"] < 0:
            raise CommandError("--recent must be zero or more days")
        day_from, day_to = self._window(options)
        client = self._client()

        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Pulling reference data from {client.base_url}, days "
                f"{day_from} to {day_to or 'the latest'}{label}"
            )
        )
        self._commit = commit
        self._verbosity = verbosity
        self._outcomes: dict[str, Counter[str]] = {f: Counter() for f in OUTCOMES}
        # Places a dry run would create, by service uuid, so their weather
        # counts as created rather than failing.
        self._planned: set[str] = set()
        # Places the service has tombstoned, by service uuid: their days
        # are skipped, not failed, since this run just buried the place.
        self._buried: set[str] = set()
        # Every place's label by service uuid, filled from the estate (pulled
        # whole), so the weather countdown names a place without a query.
        self._places: dict[str, str] = {}
        self._index = ReferenceIndex.load()
        try:
            self._pull_all(client, day_from=day_from, day_to=day_to)
        except FeedError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            self._tally_all()

        failed = sum(c[FAILED] for c in self._outcomes.values())
        if failed:
            raise CommandError(
                f"{COMMAND_NAME} finished with {failed} failed item(s). "
                "Check the log for details."
            )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    "Done." if commit else "Read-only run complete. Pass --commit."
                )
            )

    def _pull_all(
        self, client: FeedClient, *, day_from: dt.date, day_to: dt.date | None
    ) -> None:
        """Pull the estate, apply its tombstones, then the time series.

        Args:
            client: The feed client.
            day_from: The time series' first day.
            day_to: Their last day, or None for no bound.

        """
        tombstones: dict[str, list[dict[str, Any]]] = {f: [] for f in REFERENCE_FEEDS}
        deferred: dict[str, list[dict[str, Any]]] = {f: [] for f in REFERENCE_FEEDS}
        for feed in REFERENCE_FEEDS:
            self._pull_reference(client, feed, tombstones[feed], deferred[feed])
        for feed in TOMBSTONE_ORDER:
            for item in tombstones[feed]:
                self._record(feed, item, self._burier(feed))
        # A pass or resort recreated under a new uuid wanted a slug its old
        # row held; that row is gone (or detached, and so adoptable) now.
        for feed in REFERENCE_FEEDS:
            for item in deferred[feed]:
                self._record(feed, item, self._applier(feed))
            if deferred[feed] and self._verbosity >= 1:
                self.stdout.write(
                    f"{feed}: {len(deferred[feed])} retried after the tombstones"
                )
        for feed in SERIES_FEEDS:
            self._pull_series(client, feed, day_from=day_from, day_to=day_to)

    def _applier(self, feed: str) -> Callable[[dict[str, Any]], str]:
        """Return the live-item apply for ``feed``, bound to this run."""

        def apply(item: dict[str, Any]) -> str:
            """Apply one live item of ``feed``."""
            return REFERENCE_APPLY[feed](item, commit=self._commit, index=self._index)

        return apply

    def _burier(self, feed: str) -> Callable[[dict[str, Any]], str]:
        """Return the tombstone apply for ``feed``, bound to this run."""

        def bury(item: dict[str, Any]) -> str:
            """Apply one tombstone of ``feed``."""
            return apply_tombstone(feed, item, commit=self._commit, index=self._index)

        return bury

    def _tally_all(self) -> None:
        """Record every feed's outcomes in the run register, zeros included.

        ``failed`` is tallied under its bare name too, which is what marks
        the run PARTIAL in the register.
        """
        for feed, counts in self._outcomes.items():
            for outcome in OUTCOMES[feed]:
                self.tally(f"{feed}_{outcome}", counts[outcome])
        self.tally("failed", sum(c[FAILED] for c in self._outcomes.values()))

    def _window(self, options: dict[str, Any]) -> tuple[dt.date, dt.date | None]:
        """Return the first and last day of the time series to pull.

        Args:
            options: The parsed options.

        Returns:
            ``(day_from, day_to)``; ``day_to`` None for no upper bound.

        """
        if options["golden_week"]:
            days = golden_week_dates()
            return days[0], days[-1]
        return timezone.localdate() - dt.timedelta(days=options["recent"]), None

    def _client(self) -> FeedClient:
        """Build the v2 feed client from settings, refusing a misconfiguration.

        Returns:
            The client.

        Raises:
            CommandError: A setting is missing or unusable, or the URL points
                at this site.

        """
        token = getattr(settings, "DATA_FEED_V2_TOKEN", "")
        required = (
            ("DATA_FEED_URL",)
            if token
            else ("DATA_FEED_URL", "DATA_FEED_CLIENT_ID", "DATA_FEED_CLIENT_SECRET")
        )
        missing = [name for name in required if not getattr(settings, name, "")]
        if missing:
            raise CommandError(
                f"{', '.join(missing)} not set — set DATA_FEED_V2_TOKEN to a "
                "token minted for /api/data/v2/, or a service client's id and "
                "secret (docs/data-feed.md)."
            )
        try:
            client = FeedClient(
                v2_url(settings.DATA_FEED_URL),
                settings.DATA_FEED_CLIENT_ID,
                settings.DATA_FEED_CLIENT_SECRET,
                token=token,
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

    def _walk(
        self, client: FeedClient, feed: str, **params: str | None
    ) -> Iterator[tuple[list[dict[str, Any]], Iterator[dict[str, Any]]]]:
        """Walk ``feed`` one page at a time, counting its items down as applied.

        Pages, so a page's lookups are read once; each page comes with an
        iterator over its items that prints the countdown as it is reached.

        Args:
            client: The feed client.
            feed: The feed's name.
            **params: ``day_from`` / ``day_to`` for a time series.

        Yields:
            ``(items, counted)``: the page's items, and the same items
            through the countdown.

        """
        pages = client.pages(feed, **params)
        first = next(pages)
        remaining = first.count if first.count is not None else len(first.results)
        self.stdout.write(self.style.MIGRATE_LABEL(f"{feed}: {remaining} item(s)"))
        describe = describe_item(feed, self._places)
        page = first
        while True:
            # SNOW-602 exempt: the unit of work is a feed item fetched over
            # HTTPS, not a row of a local queryset; countdown counts down
            # the total the feed's first page reports.
            yield (
                page.results,
                countdown(
                    self,
                    page.results,
                    total=max(remaining, len(page.results)),
                    verbosity=self._verbosity,
                    label=f"{feed} item(s)",
                    describe=describe,
                ),
            )
            remaining -= len(page.results)
            nxt = next(pages, None)
            if nxt is None:
                return
            page = nxt

    def _record(
        self, feed: str, item: dict[str, Any], apply: Callable[[dict[str, Any]], str]
    ) -> str:
        """Apply one item, turning any failure into ``failed``, and count it.

        One bad item must not stop a pull of thousands, so every exception
        is caught, logged with its traceback and counted; a failed count
        above zero makes the command exit non-zero.

        Args:
            feed: The feed's name.
            item: The item.
            apply: Applies it and returns the outcome.

        Returns:
            The outcome.

        """
        try:
            outcome = str(apply(item))
        except Exception:
            # Broad on purpose: a failure is any exception the owning write
            # path raises (a constraint, an unknown region, a malformed
            # item), and each is reported per item, not fatal.
            logger.exception(
                "%s: %s item failed: %s", COMMAND_NAME, feed, item_key(item)
            )
            outcome = FAILED
        if outcome != DEFERRED:
            self._outcomes[feed][outcome] += 1
        return outcome

    def _report(self, feed: str) -> None:
        """Print one feed's counts."""
        if self._verbosity >= 1:
            counts = self._outcomes[feed]
            self.stdout.write(
                f"{feed}: "
                + ", ".join(f"{counts[o]} {o}" for o in OUTCOMES[feed])
                + ("" if self._commit else " (would be)")
            )

    def _pull_reference(
        self,
        client: FeedClient,
        feed: str,
        tombstones: list[dict[str, Any]],
        deferred: list[dict[str, Any]],
    ) -> None:
        """Apply every live item of one estate feed; set its tombstones aside.

        Args:
            client: The feed client.
            feed: ``locations``, ``passes`` or ``resorts``.
            tombstones: Filled with the feed's tombstoned items.
            deferred: Filled with items whose slug a tombstoned row holds.

        """
        apply_item = REFERENCE_APPLY[feed]

        def apply(i: dict[str, Any], **kwargs: Any) -> str:
            """Apply one item, deferring it when its slug is taken."""
            try:
                return apply_item(i, **kwargs)
            except SlugTaken:
                deferred.append(i)
                return DEFERRED

        for _page, items in self._walk(client, feed):
            for item in items:
                if feed == "locations":
                    self._note_place(item)
                if item.get("deleted_at"):
                    tombstones.append({"uuid": item["uuid"]})
                    continue
                outcome = self._record(
                    feed,
                    item,
                    lambda i: apply(i, commit=self._commit, index=self._index),
                )
                if feed == "locations" and outcome == CREATED and not self._commit:
                    self._planned.add(str(item["uuid"]))
        self._report(feed)

    def _note_place(self, item: dict[str, Any]) -> None:
        """Remember one place's label, and whether the service buried it.

        Args:
            item: A v2 ``locations`` item, tombstone or live.

        """
        uuid = str(item["uuid"])
        self._places[uuid] = place_label(item)
        if item.get("deleted_at"):
            self._buried.add(uuid)

    def _pull_series(
        self,
        client: FeedClient,
        feed: str,
        *,
        day_from: dt.date,
        day_to: dt.date | None,
    ) -> None:
        """Apply one time-series feed's items for the window of days.

        Args:
            client: The feed client.
            feed: ``bulletins``, ``day-ratings`` or ``weather``.
            day_from: The first day.
            day_to: The last day, or None for no bound.

        """
        run = (
            PipelineRun.objects.create(triggered_by=COMMAND_NAME)
            if self._commit and feed == "bulletins"
            else None
        )
        if run is not None:
            run.mark_running()
        try:
            for page, items in self._walk(
                client,
                feed,
                day_from=day_from.isoformat(),
                day_to=day_to.isoformat() if day_to else None,
            ):
                self._apply_page(feed, page, items, run)
        except FeedError as exc:
            if run is not None:
                run.mark_failed(exc)
            raise
        if run is not None:
            counts = self._outcomes[feed]
            run.mark_finished(counts[CREATED], counts[UPDATED])
        self._report(feed)

    def _apply_page(
        self,
        feed: str,
        page: list[dict[str, Any]],
        items: Iterator[dict[str, Any]],
        run: PipelineRun | None,
    ) -> None:
        """Apply one page of a time series, its lookups read once.

        Args:
            feed: ``bulletins``, ``day-ratings`` or ``weather``.
            page: The page's items, for its lookups.
            items: The same items, through the countdown.
            run: The bulletins ``PipelineRun``, when committing bulletins.

        """
        commit = self._commit
        if feed == "day-ratings":
            for item in items:
                self._record(
                    feed, item, lambda i: apply_reference_day_rating(i, commit=commit)
                )
            return
        try:
            lookups = (
                lookups_for("bulletins", page)
                if feed == "bulletins"
                else reference_weather_lookups(page, planned=self._planned)
            )
        except Exception:
            # Broad on purpose: a malformed item must cost only itself, so
            # each item then reads its own lookups.
            logger.exception("%s: %s page lookups failed", COMMAND_NAME, feed)
            lookups = None
        for item in items:
            if feed == "bulletins":
                self._record(
                    feed,
                    item,
                    lambda i: apply_bulletin(
                        i,
                        commit=commit,
                        run=run,
                        lookups=lookups or lookups_for("bulletins", [i]),
                    ),
                )
            elif str(item.get("location")) in self._buried:
                self._record(feed, item, lambda i: SKIPPED)
            else:
                self._record(
                    feed,
                    item,
                    lambda i: apply_weather(
                        i,
                        commit=commit,
                        lookups=lookups
                        or reference_weather_lookups([i], planned=self._planned),
                    ),
                )
