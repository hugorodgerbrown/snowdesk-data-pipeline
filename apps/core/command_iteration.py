"""
apps/core/command_iteration.py — Shared streaming + countdown helpers.

Any management command that loops over a queryset with a plain
``for obj in qs:`` loads every matching row into memory before the loop body
runs a single time — invisible on a small dev database, fatal on a
production-sized table (SNOW-602). The fix has two parts, both applied at
every call site via this module rather than copied thirteen times:

1. **Stream, don't materialise.** Order ``-id`` and iterate via
   ``.iterator()`` so memory stays bounded regardless of table size.
   Descending id ordering has a correctness benefit too — rows created while
   the command runs sort *ahead* of the cursor and are never re-visited.
2. **Countdown output.** Print each row's id (or a caller-supplied
   description) as it is processed, so stdout reads as a countdown to 1 on
   a long unattended run — progress and remaining work visible at a glance
   without a separate progress counter.

``iterate_rows`` covers the common case: the unit of work is a model row
with a primary key. ``countdown`` covers the minority case where the unit of
work is a derived value with no id of its own (e.g. a ``(region, date)``
pair) — there, the countdown counts down a known total instead of an id.

``non_negative_float`` is unrelated to iteration, but lives here for the
same reason: it is an argparse ``type=`` helper that half a dozen fetch/link
commands each need for a ``--delay`` argument, and this is where cross-app
command call sites already look for a shared helper rather than copying one.

Lives in ``apps/core/`` alongside the other flat cross-app helpers
(``freshness.py``, ``coordinates.py``, ``http.py``) since every app with
management commands needs it.
"""

from __future__ import annotations

import logging
from argparse import ArgumentTypeError
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from django.core.management.base import BaseCommand

_T = TypeVar("_T")


def non_negative_float(raw: str) -> float:
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


def announce_link_run(
    cmd: BaseCommand,
    *,
    logger: logging.Logger,
    command_name: str,
    banner: str,
    candidate_count: int,
    commit: bool,
    delay: float,
) -> None:
    """
    Write the shared start-of-run banner and log line for a ``link_*`` command.

    Every ``link_*`` backfill command (``link_resort_forecast_points``,
    ``link_region_centroid_locations``, ``link_location_forecast_cells``)
    opens the same way: a ``MIGRATE_HEADING`` banner suffixed
    ``" [READ-ONLY]"`` unless ``--commit`` was passed, and a matching
    ``"<command> started: candidates=%d commit=%s delay=%s"`` log line.
    This is that shared shape.

    Args:
        cmd: The calling command, used for ``cmd.stdout``/``cmd.style``.
        logger: The calling module's own logger, so the log record keeps
            its original module-qualified name rather than this module's.
        command_name: The command's name, as it appears in its own log
            lines (e.g. ``"link_resort_forecast_points"``).
        banner: The caller's descriptive sentence, with no flag suffix —
            this appends it.
        candidate_count: How many candidates were found.
        commit: Whether ``--commit`` was passed.
        delay: The resolved ``--delay`` value.

    """
    flag_label = "" if commit else " [READ-ONLY]"
    cmd.stdout.write(cmd.style.MIGRATE_HEADING(f"{banner}{flag_label}"))
    logger.info(
        "%s started: candidates=%d commit=%s delay=%s",
        command_name,
        candidate_count,
        commit,
        delay,
    )


def iterate_rows(
    cmd: BaseCommand,
    queryset: Any,
    *,
    verbosity: int,
    chunk_size: int | None = None,
    describe: Callable[[Any], object] | None = None,
) -> Iterator[Any]:
    """
    Stream a queryset newest-row-first, printing a countdown line per row.

    Orders ``queryset`` by ``-id`` and iterates it via ``.iterator()`` so the
    full result set is never materialised in memory. At ``verbosity >= 1``,
    writes one line per row before yielding it, so stdout reads as a
    countdown to 1 on a long-running command.

    ``queryset`` is typed ``Any`` rather than ``QuerySet[Model]`` because a
    handful of call sites stream a ``values_list(...)`` queryset (rows are
    plain tuples, not model instances) — those callers must always pass
    ``describe``, since a tuple has no ``.pk``.

    Args:
        cmd: The calling command, used for ``cmd.stdout`` so output honours
            the command's own stream (and is easy to capture in tests).
        queryset: The queryset to stream. Any existing ``order_by`` is
            overridden with ``-id``.
        verbosity: Django's ``--verbosity`` level; countdown lines are
            suppressed at ``0``.
        chunk_size: Forwarded to ``.iterator()``. Required (by Django) when
            ``queryset`` carries a ``prefetch_related`` — pass it explicitly
            at every such call site or Django raises.
        describe: Optional callable returning the token to print for a row,
            in place of the bare primary key (e.g. ``lambda b: b.bulletin_id``
            for a command that already surfaces a domain id, or a
            ``values_list`` tuple's own id element).

    Yields:
        Each row of ``queryset``, ordered newest id first.

    """
    ordered = queryset.order_by("-id")
    iterator_kwargs: dict[str, int] = {}
    if chunk_size is not None:
        iterator_kwargs["chunk_size"] = chunk_size

    for row in ordered.iterator(**iterator_kwargs):
        if verbosity >= 1:
            token = describe(row) if describe is not None else row.pk
            cmd.stdout.write(str(token))
        yield row


def countdown(
    cmd: BaseCommand,
    items: Iterable[_T],
    *,
    total: int,
    verbosity: int,
    label: str,
) -> Iterator[_T]:
    """
    Drive a loop over derived (non-row) items, printing a decreasing count.

    For loops whose unit of work has no primary key of its own — a derived
    ``(region, date)`` pair, for instance — there is nothing to print a
    ``-id`` countdown against. This prints ``"N <label> remaining"`` before
    each item instead, counting down from ``total`` to ``1``.

    Args:
        cmd: The calling command, used for ``cmd.stdout``.
        items: The items to iterate. May be a one-pass iterator (e.g. a
            streamed ``values_list().iterator()``) — the caller must supply
            ``total`` separately since it cannot be derived from ``items``
            without materialising it.
        total: The total number of items, for the countdown's starting
            value. Typically a ``.count()`` taken before streaming ``items``.
        verbosity: Django's ``--verbosity`` level; countdown lines are
            suppressed at ``0``.
        label: A short noun describing one item (e.g. ``"pair(s)"``), used
            in the printed line.

    Yields:
        Each item of ``items``, unchanged.

    """
    remaining = total
    for item in items:
        if verbosity >= 1:
            cmd.stdout.write(f"{remaining} {label} remaining")
        remaining -= 1
        yield item
