"""
tests/e2e/test_pwa_push_journey.py — SNOW-389 real-SW push journey.

Push isn't a numbered scenario in docs/testing-scenarios.md §"PWA Shell" —
it's the residual set docs/telemetry-pipeline.md's "Not covered by
Playwright" section calls out (``pwa.push.received`` / ``.shown`` /
``.opened``). The Q2 spike (``tests/e2e/_spike_results.py``) found
``pwa.push.received`` / ``.shown`` ARE reliably drivable: dispatching a
synthetic ``PushEvent`` directly on the real, activated SW invokes
``sw.js``'s own ``push`` listener exactly as a live push message would,
with no push subscription or push-service round-trip needed.

``pwa.push.opened`` (a ``notificationclick`` event) does NOT ship here —
discovered during implementation, one level deeper than the spike's own
scope covered. Headless Chromium's ``Notification.permission`` reports
``'denied'`` regardless of the Playwright ``notifications`` permission
grant, and ``showNotification()`` resolves as a silent no-op rather than
rejecting or actually displaying anything — ``registration.
getNotifications()`` is unconditionally empty afterwards, in the SW
context and from the page, at any delay. There is no queryable
``Notification`` instance available to build a synthetic
``notificationclick`` event against. ``.opened`` stays manual — see
``docs/telemetry-pipeline.md``.

A second, narrower finding shaped this file's shape: issuing ANY further
Playwright/CDP round trip — a second ``page.wait_for_function()``, a
follow-up ``page.evaluate()`` to re-read the same IndexedDB rows for a
property check, even extracting a value via the first call's returned
handle's ``json_value()`` — reliably came back empty in repeated manual
testing, despite the underlying data genuinely being there a moment
before. The reliable pattern: ONE ``wait_for_function()`` call whose
predicate embeds every assertion this test needs (event presence, URL
property, registration count) and returns a single boolean, with nothing
further evaluated on the page afterwards.
"""

from __future__ import annotations

from tests.e2e.conftest import PwaPage


def test_push_event_dispatched_to_real_sw_emits_received_and_shown(
    pwa_page: PwaPage,
) -> None:
    """A synthetic push event drives sw.js's receive -> show funnel.

    Dispatching ``new PushEvent('push', ...)`` on the real SW invokes its
    listener, which calls ``showNotification()`` and posts
    ``pwa.push.received`` then ``pwa.push.shown`` (only after
    ``showNotification()``'s promise resolves, so a suppressed
    notification would not falsely report "shown"). The single predicate
    below also folds in the SW invariant (still exactly one registration)
    — see the module docstring for why every assertion has to live inside
    this one round trip.
    """
    page = pwa_page.page

    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    worker.evaluate(
        """() => {
            self.dispatchEvent(new PushEvent('push', {
              data: JSON.stringify({
                url: '/', title: 'Snowdesk test', body: 'push journey body',
              }),
            }));
          }"""
    )

    page.wait_for_function(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            const received = rows.find((r) => r.event === 'pwa.push.received');
            const shown = rows.find((r) => r.event === 'pwa.push.shown');
            const regs = await navigator.serviceWorker.getRegistrations();
            return !!(
              received && received.properties.url === '/' &&
              shown && shown.properties.url === '/' &&
              regs.length === 1
            );
          }""",
        timeout=5000,
    )
