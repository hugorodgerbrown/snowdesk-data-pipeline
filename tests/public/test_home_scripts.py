"""
tests/public/test_home_scripts.py — Script-tag hygiene for the map homepage.

Covers:
  - ``static/js/place_picker.js`` is referenced exactly once on ``/``
    (C5, 2026-08-03 JS review). The file was loaded from both
    ``_report_surface.html`` and ``_favourites_surface.html``, which
    ``home.html`` includes back-to-back. Browsers do not deduplicate
    script elements by URL, so the IIFE ran twice and the second
    ``window.PlacePicker`` overwrote the first, orphaning its closure.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
class TestHomeScriptTags:
    """The homepage must not load any shared module more than once."""

    def test_place_picker_loaded_once(self) -> None:
        """``place_picker.js`` appears in exactly one <script> src on ``/``."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert content.count("js/place_picker.js") == 1

    def test_htmx_loaded_once(self) -> None:
        """``htmx.min.js`` — the other module shared by both surfaces — stays single."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert content.count("js/htmx.min.js") == 1

    def test_place_picker_precedes_its_consumers(self) -> None:
        """The single ``place_picker.js`` tag stays ahead of both consumers.

        ``report.js`` and ``favourites.js`` both drive ``window.PlacePicker``
        and all three are ``defer``, which executes in document order — so
        hoisting the tag must not move it past the surfaces that use it.
        """
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        picker = content.index("js/place_picker.js")
        assert picker < content.index("js/report.js")
        assert picker < content.index("js/favourites.js")
