"""
tests/core/test_command_iteration.py — Tests for apps.core.command_iteration.

Covers ``iterate_rows`` (newest-id-first streaming with a per-row countdown
line) and ``countdown`` (decreasing-remaining-count driver for derived,
non-row units of work) — the two shared helpers every SNOW-602 management
command call site routes through instead of a hand-rolled loop.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.core.command_iteration import countdown, iterate_rows


class _FakeStdout:
    """Collects ``.write()`` calls, mirroring ``BaseCommand.stdout``."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, msg: object) -> None:
        """Record one line of output."""
        self.lines.append(str(msg))


class _FakeCommand:
    """A minimal stand-in for ``BaseCommand`` carrying only ``stdout``."""

    def __init__(self) -> None:
        self.stdout = _FakeStdout()


class _Row:
    """A minimal stand-in for a model instance with a primary key."""

    def __init__(self, pk: int, bulletin_id: str = "") -> None:
        self.pk = pk
        self.id = pk
        self.bulletin_id = bulletin_id


class _SpyQuerySet:
    """
    A queryset spy that records ``order_by``/``iterator`` calls.

    ``rows`` must already be supplied in the order the "database" would
    return them for the ``-id`` ordering under test (descending by pk) —
    the spy doesn't re-sort, it just records what it was asked to do.
    """

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.order_by_calls: list[tuple[str, ...]] = []
        self.iterator_calls: list[dict[str, Any]] = []

    def order_by(self, *fields: str) -> "_SpyQuerySet":
        """Record the ordering requested and return self (chainable)."""
        self.order_by_calls.append(fields)
        return self

    def iterator(self, **kwargs: Any) -> list[_Row]:
        """Record the call and return the (already-ordered) rows."""
        self.iterator_calls.append(kwargs)
        return self._rows


# iterate_rows and countdown are typed against BaseCommand / QuerySet — the
# fakes above deliberately don't subclass either (they're the minimal shape
# the helpers actually use), so every call site below is typed `Any` rather
# than fighting mypy over structural compatibility it can't see.


class TestIterateRows:
    """Tests for iterate_rows."""

    def test_orders_by_descending_id(self) -> None:
        """The queryset is re-ordered by -id regardless of its prior ordering."""
        qs: Any = _SpyQuerySet([_Row(3), _Row(2), _Row(1)])
        cmd: Any = _FakeCommand()

        list(iterate_rows(cmd, qs, verbosity=1))

        assert qs.order_by_calls == [("-id",)]

    def test_streams_via_iterator_not_materialised(self) -> None:
        """Rows are pulled through .iterator(), never a plain list()/all()."""
        qs: Any = _SpyQuerySet([_Row(3), _Row(2), _Row(1)])
        cmd: Any = _FakeCommand()

        list(iterate_rows(cmd, qs, verbosity=1))

        assert len(qs.iterator_calls) == 1

    def test_yields_newest_id_first(self) -> None:
        """Rows are yielded in the order the (already -id-ordered) queryset gives them."""
        qs: Any = _SpyQuerySet([_Row(30), _Row(20), _Row(10)])
        cmd: Any = _FakeCommand()

        pks: list[int] = [row.pk for row in iterate_rows(cmd, qs, verbosity=1)]

        assert pks == [30, 20, 10]

    def test_writes_one_countdown_line_per_row_at_verbosity_1(self) -> None:
        """Each row prints its bare pk as a countdown line by default."""
        qs: Any = _SpyQuerySet([_Row(3), _Row(2), _Row(1)])
        cmd: Any = _FakeCommand()

        list(iterate_rows(cmd, qs, verbosity=1))

        assert cmd.stdout.lines == ["3", "2", "1"]

    def test_writes_nothing_at_verbosity_0(self) -> None:
        """Countdown lines are suppressed at verbosity 0, rows still yielded."""
        qs: Any = _SpyQuerySet([_Row(3), _Row(2), _Row(1)])
        cmd: Any = _FakeCommand()

        pks: list[int] = [row.pk for row in iterate_rows(cmd, qs, verbosity=0)]

        assert pks == [3, 2, 1]
        assert cmd.stdout.lines == []

    def test_describe_overrides_the_printed_token(self) -> None:
        """A describe callable replaces the bare pk in the countdown line."""
        qs: Any = _SpyQuerySet([_Row(3, "FR-01"), _Row(2, "FR-02")])
        cmd: Any = _FakeCommand()

        list(iterate_rows(cmd, qs, verbosity=1, describe=lambda row: row.bulletin_id))

        assert cmd.stdout.lines == ["FR-01", "FR-02"]

    def test_chunk_size_is_forwarded(self) -> None:
        """A given chunk_size reaches .iterator() untouched."""
        qs: Any = _SpyQuerySet([_Row(1)])
        cmd: Any = _FakeCommand()

        list(iterate_rows(cmd, qs, verbosity=1, chunk_size=200))

        assert qs.iterator_calls == [{"chunk_size": 200}]

    def test_no_chunk_size_by_default(self) -> None:
        """Without an explicit chunk_size, .iterator() is called with no kwargs."""
        qs: Any = _SpyQuerySet([_Row(1)])
        cmd: Any = _FakeCommand()

        list(iterate_rows(cmd, qs, verbosity=1))

        assert qs.iterator_calls == [{}]


class TestCountdown:
    """Tests for countdown."""

    def test_prints_a_decreasing_remaining_count(self) -> None:
        """Each item is preceded by a 'N label remaining' line, counting down."""
        cmd: Any = _FakeCommand()
        items = ["a", "b", "c"]

        result = list(countdown(cmd, items, total=3, verbosity=1, label="pair(s)"))

        assert result == ["a", "b", "c"]
        assert cmd.stdout.lines == [
            "3 pair(s) remaining",
            "2 pair(s) remaining",
            "1 pair(s) remaining",
        ]

    def test_writes_nothing_at_verbosity_0(self) -> None:
        """Countdown lines are suppressed at verbosity 0, items still yielded."""
        cmd: Any = _FakeCommand()

        result = list(countdown(cmd, ["x", "y"], total=2, verbosity=0, label="pair(s)"))

        assert result == ["x", "y"]
        assert cmd.stdout.lines == []

    def test_works_over_a_one_pass_iterator(self) -> None:
        """countdown doesn't require len() — a generator works with an explicit total."""
        cmd: Any = _FakeCommand()

        def _gen() -> Any:
            yield "p"
            yield "q"

        result = list(countdown(cmd, _gen(), total=2, verbosity=1, label="item(s)"))

        assert result == ["p", "q"]
        assert cmd.stdout.lines == [
            "2 item(s) remaining",
            "1 item(s) remaining",
        ]

    @pytest.mark.parametrize("total", [0])
    def test_empty_input_writes_nothing(self, total: int) -> None:
        """An empty iterable produces no output and no items."""
        cmd: Any = _FakeCommand()

        result: list[Any] = list(
            countdown(cmd, [], total=total, verbosity=1, label="pair(s)")
        )

        assert result == []
        assert cmd.stdout.lines == []
