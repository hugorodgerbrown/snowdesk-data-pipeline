"""
core/models.py — Shared abstract Django models.

Holds ``BaseModel`` so concrete-model apps (``regions``, ``bulletins``,
``subscriptions``, …) share a single source of truth for the standard
fields without depending on each other.
"""

from __future__ import annotations

import uuid

from django.db import models


class BaseModel(models.Model):
    """
    Abstract base model providing standard fields for all concrete models.

    Provides a BigAutoField primary key, a mutable uuid4 field, and
    created_at / updated_at timestamps.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.__class__.__name__}({self.pk})"
