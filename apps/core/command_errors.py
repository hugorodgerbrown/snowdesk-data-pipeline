"""
apps/core/command_errors.py — Command errors that carry how they were reported.

A failure should reach Sentry once, from the place it started (SNOW-1425).
``LoggingIntegration`` turns every ERROR log into an event, so a failure that
is logged where it starts and again by each layer that passes it on files
one event per layer: a timed-out ALBINA fetch filed three.

``ReportedCommandError`` is a ``CommandError`` that says "this has already
been logged at ERROR". A caller that catches it logs at WARNING instead, and
still fails: the subclass keeps ``CommandError``'s non-zero exit, and the
command register records the run as FAILED as it does for any
``CommandError``.
"""

from __future__ import annotations

import logging

from django.core.management.base import CommandError

logger = logging.getLogger(__name__)


class ReportedCommandError(CommandError):
    """A command failure already logged at ERROR where it started.

    Raise it instead of ``CommandError`` when the cause has been logged at
    ERROR already (for example by ``PipelineRun.mark_failed``). Catch it to
    log at WARNING; log any other ``CommandError`` at ERROR.
    """
