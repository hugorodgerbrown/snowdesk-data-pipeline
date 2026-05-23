"""
core/management/commands/run_scheduler.py — APScheduler management command entry point.

Builds the scheduler via :func:`schedule.build_scheduler` and calls
``.start()``, which blocks until the process receives SIGINT or SIGTERM.
APScheduler's :class:`BlockingScheduler` handles both signals cleanly,
logging a shutdown notice before exiting.

Usage::

    python manage.py run_scheduler

No arguments are required. ``--verbosity`` is inherited from
:class:`~django.core.management.base.BaseCommand` and controls Django's
standard log routing; schedule-level logging goes through the Python
``logging`` module (see ``LOGGING`` in ``config/settings/base.py``).

This command is the ``startCommand`` for the ``snowdesk-scheduler`` Render
Background Worker (see ``render.yaml``).
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Start the APScheduler blocking scheduler.

    Blocks until interrupted (SIGINT/SIGTERM). Suitable as a long-running
    Render Background Worker ``startCommand``.
    """

    help = (
        "Start the APScheduler job runner. Blocks until interrupted. "
        "Runs fetch_bulletins and fetch_weather on their configured cron "
        "schedules via django.core.management.call_command."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Build the scheduler and start blocking execution."""
        import schedule as schedule_module

        scheduler = schedule_module.build_scheduler()
        job_ids = [job.id for job in scheduler.get_jobs()]
        logger.info(
            "run_scheduler: starting with %d job(s): %s",
            len(job_ids),
            job_ids,
        )
        if options["verbosity"] >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Scheduler starting with {len(job_ids)} job(s): "
                    f"{', '.join(job_ids)}"
                )
            )

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("run_scheduler: shutdown signal received — stopping gracefully")
            if options["verbosity"] >= 1:
                self.stdout.write("Scheduler stopped.")
