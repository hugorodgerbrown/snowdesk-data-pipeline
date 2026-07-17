"""
tests/e2e/_spike_results.py — SNOW-389 spike outcomes.

Recorded findings from the plan's step-1 spike, answering the five open
questions from the Linear scoping comment before any real lifecycle test
was written. The throwaway harnesses that produced these results
(``_spike_sw_update.py``, ``_spike_push_dispatch.py``) are deleted once
this module lands — this is the surviving record.

Referenced when writing the coverage annotations in
``docs/testing-scenarios.md`` and the "Not covered by Playwright" section
of ``docs/telemetry-pipeline.md``.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class SpikeResult(TypedDict):
    """One spike question's outcome.

    Attributes:
        status: ``"covered"`` — the real test ships. ``"manual"`` — the
            scenario stays a manual-only DevTools walkthrough.
        reason: One-line justification, written for a reviewer or a future
            reader of ``docs/testing-scenarios.md`` judging it cold.

    """

    status: Literal["covered", "manual"]
    reason: str


SPIKE_RESULTS: dict[str, SpikeResult] = {
    "Q1_sw_update": {
        "status": "covered",
        "reason": (
            "30/30 green. Playwright's page.route() / context.route() never "
            "observe a service worker's own script fetch (neither the "
            "initial registration fetch nor a registration.update() "
            "re-fetch) — confirmed by a page.on('request', ...) listener "
            "that never fired for /sw.js either. The working mechanism "
            "instead monkeypatches public.views._serve_sw_file on the "
            "live_server's in-process Django app (live_server runs in a "
            "background thread in the same interpreter, not a subprocess) "
            "so the *server* returns byte-different content on the second "
            "fetch — a more faithful analogue of a real deploy than "
            "client-side interception would have been anyway. Drives P4."
        ),
    },
    "Q2_push_dispatch": {
        "status": "covered",
        "reason": (
            "10/10 green. context.service_workers[0].evaluate() can "
            "dispatch `new PushEvent('push', {data: ...})` directly on the "
            "real, activated SW, invoking sw.js's push listener exactly as "
            "a live push message would. showNotification() does not "
            "reject in headless Chromium once the notifications permission "
            "is granted (see Q4). Drives the push trio in "
            "test_pwa_push_journey.py."
        ),
    },
    "Q3_activation_failed_fetch_undefined": {
        "status": "manual",
        "reason": (
            "No spike run — accepted manual-only per the approved scope. "
            "pwa.sw.activation_failed and pwa.sw.fetch_undefined both "
            "require provoking a genuine SW-internal failure (a thrown "
            "activate handler, a strategy function resolving to a "
            "non-Response) that this harness has no seam to trigger "
            "without editing static/js/sw.js, which is out of scope."
        ),
    },
    "Q4_notifications_permission": {
        "status": "covered",
        "reason": (
            "Folded into Q2 — browser_context_args now grants "
            "'notifications' repo-wide (tests/e2e/conftest.py)."
        ),
    },
    "Q5_signed_in_helper_cost": {
        "status": "covered",
        "reason": (
            "Measured end-to-end (SubscriberFactory.create() + "
            "force_login() + context.add_cookies(), including the "
            "pwa_page navigation + SW-activation wait it chains on) at "
            "~0.26s per pytest --durations=0 — well under the plan's 2s "
            "cutoff. signed_in_page ships as a single fixture; P12's full "
            "manage-page journey is not split into a cheaper subset."
        ),
    },
}
