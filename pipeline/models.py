"""
pipeline/models.py — Database models for the pipeline application.

Defines two core models:
  - PipelineRun: records each execution of the data pipeline (scheduled or
    manual), its status, and timing metadata.
  - DataRecord: stores individual data items fetched by the pipeline, keyed
    by an external identifier and the date they relate to.

Keep business logic out of models — put it in pipeline/services/ instead.
"""

import logging

from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


class PipelineRun(models.Model):
    """
    Represents a single execution of the data-fetching pipeline.

    Tracks whether the run succeeded or failed, how long it took, and how
    many records were created or updated.
    """

    class Status(models.TextChoices):
        """Possible states for a pipeline run."""

        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    triggered_by = models.CharField(
        max_length=64,
        default="unknown",
        help_text="Who or what triggered this run (e.g. 'scheduler', 'backfill', 'manual').",
    )

    class Meta:
        """Model metadata."""

        ordering = ["-started_at"]

    def __str__(self):
        """Return a human-readable representation."""
        return f"PipelineRun({self.pk}, {self.status}, {self.started_at:%Y-%m-%d %H:%M})"

    def mark_running(self):
        """Transition the run to the RUNNING state and persist."""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status"])
        logger.info("PipelineRun %s started", self.pk)

    def mark_success(self, records_created: int, records_updated: int):
        """
        Transition the run to SUCCESS and record counts.

        Args:
            records_created: Number of new DataRecord rows created.
            records_updated: Number of existing DataRecord rows updated.
        """
        self.status = self.Status.SUCCESS
        self.finished_at = timezone.now()
        self.records_created = records_created
        self.records_updated = records_updated
        self.save(update_fields=["status", "finished_at", "records_created", "records_updated"])
        logger.info(
            "PipelineRun %s succeeded: %d created, %d updated",
            self.pk,
            records_created,
            records_updated,
        )

    def mark_failed(self, error: Exception):
        """
        Transition the run to FAILED and store the error message.

        Args:
            error: The exception that caused the failure.
        """
        self.status = self.Status.FAILED
        self.finished_at = timezone.now()
        self.error_message = str(error)
        self.save(update_fields=["status", "finished_at", "error_message"])
        logger.error("PipelineRun %s failed: %s", self.pk, error, exc_info=True)

    @property
    def duration_seconds(self):
        """Return elapsed seconds, or None if the run has not finished."""
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class DataRecord(models.Model):
    """
    A single data item fetched and stored by the pipeline.

    The (external_id, date) pair is unique — use update_or_create when
    upserting records so that re-runs are idempotent.
    """

    external_id = models.CharField(max_length=255, db_index=True)
    date = models.DateField(db_index=True)
    value = models.DecimalField(max_digits=20, decimal_places=6)
    label = models.CharField(max_length=255, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pipeline_run = models.ForeignKey(
        PipelineRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="data_records",
    )

    class Meta:
        """Model metadata."""

        ordering = ["-date", "external_id"]
        unique_together = [("external_id", "date")]

    def __str__(self):
        """Return a human-readable representation."""
        return f"DataRecord({self.external_id}, {self.date}, {self.value})"
