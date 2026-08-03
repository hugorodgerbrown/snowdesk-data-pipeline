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

Lives in ``apps/core/`` alongside the other flat cross-app helpers
(``freshness.py``, ``coordinates.py``, ``http.py``) since every app with
management commands needs it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TypeVar

from django.core.management.base import BaseCommand
from django.db.models import Model, QuerySet

_M = TypeVar("_M", bound=Model)
_T = TypeVar("_T")


def iterate_rows(
    cmd: BaseCommand,
    queryset: QuerySet[_M],
    *,
    verbosity: int,
    chunk_size: int | None = None,
    describe: Callable[[_M], object] | None = None,
) -> Iterator[_M]:
    """
    Stream a queryset newest-row-first, printing a countdown line per row.

    Orders ``queryset`` by ``-id`` and iterates it via ``.iterator()`` so the
    full result set is never materialised in memory. At ``verbosity >= 1``,
    writes one line per row before yielding it, so stdout reads as a
    countdown to 1 on a long-running command.

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
            for a command that already surfaces a domain id).

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
