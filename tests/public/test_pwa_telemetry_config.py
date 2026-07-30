"""
Server-side tests for the PWA telemetry master switch.

``settings.PWA_TELEMETRY_ENABLED`` is the single operator-level off switch
for the first-party PWA telemetry pipeline (docs/telemetry-pipeline.md).
The server half is covered here:

* ``apps.public.context_processors.pwa_telemetry`` exposes the flag as a bool.
* Every public page bakes it into the ``<meta name="pwa-telemetry-enabled">``
  tag as ``"1"`` (enabled) / ``"0"`` (disabled) — the value
  ``static/js/telemetry.js`` reads to decide whether to run at all.

The client no-op behaviour driven by ``content="0"`` is exercised in
``tests/js/test_telemetry_master_switch.js`` (SNOW-496, Vitest); the
receiver and server-signal gates are covered in ``tests/analytics/``.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from apps.public.context_processors import pwa_telemetry


def test_context_processor_returns_true_by_default() -> None:
    """With the flag enabled the processor returns a plain ``True`` bool."""
    with override_settings(PWA_TELEMETRY_ENABLED=True):
        result = pwa_telemetry(HttpRequest())

    assert result == {"PWA_TELEMETRY_ENABLED": True}


def test_context_processor_returns_false_when_disabled() -> None:
    """A disabled flag is exposed as ``False``, not raised."""
    with override_settings(PWA_TELEMETRY_ENABLED=False):
        result = pwa_telemetry(HttpRequest())

    assert result == {"PWA_TELEMETRY_ENABLED": False}


@pytest.mark.django_db
@override_settings(PWA_TELEMETRY_ENABLED=True)
def test_meta_tag_enabled_on_home_page() -> None:
    """Enabled → the shell renders ``content="1"``."""
    body = Client().get("/").content.decode("utf-8")

    assert '<meta name="pwa-telemetry-enabled" content="1">' in body


@pytest.mark.django_db
@override_settings(PWA_TELEMETRY_ENABLED=False)
def test_meta_tag_disabled_on_home_page() -> None:
    """Disabled → the shell renders ``content="0"`` so telemetry.js bails."""
    body = Client().get("/").content.decode("utf-8")

    assert '<meta name="pwa-telemetry-enabled" content="0">' in body
