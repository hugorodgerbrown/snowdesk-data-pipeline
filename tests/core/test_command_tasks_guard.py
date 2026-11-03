"""
tests/core/test_command_tasks_guard.py — a bulk command is offered to the ops server.

Hugo's fifth rule for bulk commands (3 Nov 2026): a backfill, update or
clean-up runs from the ops MCP server's ``run_command``. A command that
enqueues its rows through ``apps.core.command_tasks.enqueue_each`` returns
in seconds, so it fits that tool's "finishes in minutes" rule
(docs/decisions/a-remote-command-runs-on-the-task-worker.md), and must say
``mcp_runnable = True``. This test fails one that does not, the way
``test_command_register_guard.py`` fails a command that skips the register.

The other half of rule 4, that a task calling an outside service is paced,
is enforced where it is enqueued: ``enqueue_each`` refuses a task
registered with ``calls_outside`` that is given no ``pace``.
"""

from __future__ import annotations

import inspect

import pytest
from django.core.management import get_commands, load_command_class

from apps.core.command_tasks import is_outside
from apps.locations.services.elevation import fill_elevation_task
from apps.locations.services.what3words import fill_what3words_task
from apps.routes.services.ski_tour_render import render_tour_task
from apps.routes.services.slope_segments import backfill_route_slopes_task
from apps.trips.services.slope import backfill_trip_slopes_task
from apps.weather.services.station_tasks import (
    backfill_unit_task,
    fetch_station_task,
)


def _bulk_commands() -> list[str]:
    """Return every project command whose module calls ``enqueue_each``."""
    names = []
    for name, app in get_commands().items():
        if not app.startswith("apps."):
            continue
        module = inspect.getmodule(load_command_class(app, name))
        if "enqueue_each(" in inspect.getsource(module):  # type: ignore[arg-type]
            names.append(name)
    return sorted(names)


def test_the_walk_finds_the_bulk_commands() -> None:
    """The guard is not vacuous: the SNOW-1397 to SNOW-1399 commands are found."""
    assert {
        "backfill_route_slope_samples",
        "backfill_trip_slope_samples",
        "backfill_weather",
        "fetch_weather",
        "fill_location_elevations",
        "fill_what3words",
        "rebuild_render_models",
        "rebuild_source_render_models",
        "render_ski_tours",
    } <= set(_bulk_commands())


def test_every_bulk_command_is_mcp_runnable() -> None:
    """A command that enqueues its rows is offered to run_command."""
    missing = [
        name
        for name in _bulk_commands()
        if not getattr(load_command_class(get_commands()[name], name), "mcp_runnable")
    ]
    assert missing == [], (
        "A command that calls enqueue_each returns in seconds; set "
        f"mcp_runnable = True on it: {missing}"
    )


@pytest.mark.parametrize(
    "the_task",
    [
        render_tour_task,
        backfill_route_slopes_task,
        backfill_trip_slopes_task,
        fill_what3words_task,
        fill_elevation_task,
        fetch_station_task,
        backfill_unit_task,
    ],
    ids=lambda t: t.name,
)
def test_the_outside_tasks_are_registered(the_task: object) -> None:
    """Each calls an outside service, so enqueue_each refuses it unpaced."""
    assert is_outside(the_task)  # type: ignore[arg-type]
