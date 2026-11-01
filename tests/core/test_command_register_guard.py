"""
tests/core/test_command_register_guard.py — every project command is recorded.

The command run register (SNOW-1189) is only deterministic if it is
complete: a backfill that bypasses it is a backfill nobody can prove ran.
Every management command under ``apps/`` must therefore inherit
``RegisteredCommand``. A new command that subclasses ``BaseCommand``
directly fails here, the same way ``bin/ds-lint`` fails a raw palette
class — a convention held by a failing build rather than by memory.

A command may still opt out with ``record_runs = False``; that is visible
in its own source, next to the comment that has to say why.
"""

from __future__ import annotations

import inspect

from django.core.management import get_commands, load_command_class

from apps.core.command_register import RegisteredCommand


def _project_commands() -> dict[str, str]:
    """Return ``{command name: app module}`` for every command under ``apps.``."""
    return {
        name: app for name, app in get_commands().items() if app.startswith("apps.")
    }


def test_there_are_project_commands_to_check() -> None:
    """The walk finds the project's commands, so the guard is not vacuous."""
    assert "relink_bulletin_regions" in _project_commands()


def test_every_project_command_is_registered() -> None:
    """No project command subclasses BaseCommand directly."""
    unregistered = sorted(
        name
        for name, app in _project_commands().items()
        if not isinstance(load_command_class(app, name), RegisteredCommand)
    )
    assert unregistered == [], (
        "Inherit apps.core.command_register.RegisteredCommand, not BaseCommand: "
        f"{unregistered}"
    )


def test_only_run_scheduler_opts_out() -> None:
    """Opting out is rare and deliberate; a new opt-out updates this list."""
    opted_out = sorted(
        name
        for name, app in _project_commands().items()
        if not getattr(load_command_class(app, name), "record_runs", True)
    )
    assert opted_out == ["run_scheduler"]


def test_only_credential_printers_skip_output_capture() -> None:
    """A command whose output is a credential keeps it out of the register.

    Pinned so adding one is a deliberate edit here, and so a command that
    prints a token, key or sign-in link is a reviewer's prompt to add it.
    """
    uncaptured = sorted(
        name
        for name, app in _project_commands().items()
        if not getattr(load_command_class(app, name), "capture_output", True)
    )
    assert uncaptured == ["dev_magic_link", "mint_feed_token", "mint_vapid_keypair"]


# The prefixes this codebase uses for a command that changes data — the
# backfills, relinks and reloads run after a deploy (SNOW-1190), plus the
# purges, syncs and fetches the scheduler runs. Each must tally what it
# changes, or its register row records that it ran and not what it did.
_WRITING_PREFIXES = (
    "backfill_",
    "compute_",
    "fetch_",
    "fill_",
    "import_",
    "link_",
    "prune_",
    "purge_",
    "refresh_",
    "rekey_",
    "relink_",
    "sync_",
    "uppercase_",
)


def test_every_data_changing_command_tallies() -> None:
    """A backfill, relink, import, purge or fetch records its counts."""
    silent = sorted(
        name
        for name, app in _project_commands().items()
        if name.startswith(_WRITING_PREFIXES)
        and "self.tally(" not in inspect.getsource(type(load_command_class(app, name)))
    )
    assert silent == [], f"Call self.tally() for what these change: {silent}"


def test_mcp_runnable_commands_are_pinned() -> None:
    """Offering a command to the ops MCP server's run_command is deliberate.

    Each one runs on the task worker that also sends email, so it must
    finish in minutes (a long job enqueues a task per row, CLAUDE.md rule
    7). Adding one is an edit here, which is a reviewer's prompt to ask.
    """
    runnable = sorted(
        name
        for name, app in _project_commands().items()
        if getattr(load_command_class(app, name), "mcp_runnable", False)
    )
    assert runnable == [
        "backfill_weather",
        "diagnose_region_coverage",
        "fetch_bulletins",
        "fetch_weather",
        "link_region_centroid_locations",
        "link_resort_locations",
        "mirror_reference_data",
        "prune_command_runs",
        "purge_expired_oauth_tokens",
        "purge_request_logs",
        "rebuild_render_models",
        "rebuild_source_render_models",
        "render_ski_tours",
        "sync_waffle_flags",
        "sync_waffle_switches",
    ]


def test_mcp_runnable_commands_are_recorded_and_captured() -> None:
    """A remotely run command is always in the register, output and all.

    The register row is the only record of a run started over MCP, and a
    command that prints a credential would put it in the task result too.
    """
    unsafe = sorted(
        name
        for name, app in _project_commands().items()
        if getattr(load_command_class(app, name), "mcp_runnable", False)
        and not (
            getattr(load_command_class(app, name), "record_runs", True)
            and getattr(load_command_class(app, name), "capture_output", True)
        )
    )
    assert unsafe == []
