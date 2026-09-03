"""
tests/public/test_debug_log_panel.py — gating for the debug-trace panel (SNOW-812).

The panel and its recorder are instrumentation, not product. The whole
cost argument for putting ``window.pwaDebugLog?.record(...)`` calls
through map.js, sw.js and the IndexedDB helpers rests on one fact: for
anyone without the ``debug_log`` flag, ``window.pwaDebugLog`` is undefined
and every one of those call sites is an optional-chain no-op. That fact is
enforced here and nowhere else — the flag is checked in a context
processor, so no view test would catch it being wired up wrongly.

Both directions matter:

- **Flag off** — neither the markup nor either script may reach the page.
  A leak here would ship a debug surface to every visitor.
- **Flag on** — all three must be present together. The panel without the
  recorder is a dead box; the recorder without the panel is a buffer
  nobody can read.

Group scoping itself (``GRP_DEBUG``) is waffle's own behaviour and is
covered where the manifest is applied, in
``tests/core/management/commands/test_sync_waffle_flags.py``.
"""

from __future__ import annotations

from typing import cast

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from waffle.models import Flag
from waffle.testutils import override_flag

# Every marker the panel puts on the page: the container, the recorder, and
# the reader. All three appear together or none of them do.
_PANEL_MARKERS = (
    'data-testid="debug-log-panel"',
    "js/debug_log.js",
    "js/debug_log_panel.js",
)


@pytest.fixture
def signed_in(client: Client) -> Client:
    """A logged-in client.

    Every flag-on assertion needs one: the context processor short-circuits
    on ``request.user.is_authenticated`` before it consults waffle at all,
    so ``override_flag`` alone does not reach an anonymous visitor. That is
    the documented contract (see ``debug_log_visible``), and these tests
    would be asserting against a path the panel deliberately never takes.
    """
    user = User.objects.create_user("debugger", password="unused-in-this-test")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_panel_is_absent_without_the_flag(client: Client) -> None:
    """No markup, no scripts, for an ordinary visitor.

    This is the load-bearing assertion of the whole ticket: it is what
    makes the instrumentation scattered across the map free for everyone
    who is not debugging it.
    """
    response = client.get(reverse("public:home"))

    assert response.status_code == 200
    body = response.content.decode()
    for marker in _PANEL_MARKERS:
        assert marker not in body


@pytest.mark.django_db
@override_flag("debug_log", active=True)
def test_panel_and_recorder_ship_together_with_the_flag(signed_in: Client) -> None:
    """With the flag on, the panel, the recorder and the reader all arrive."""
    response = signed_in.get(reverse("public:home"))

    assert response.status_code == 200
    body = response.content.decode()
    for marker in _PANEL_MARKERS:
        assert marker in body


@pytest.mark.django_db
@override_flag("debug_log", active=True)
def test_panel_ships_on_a_second_page_too(signed_in: Client) -> None:
    """Universal, not map-only — the panel comes from ``public/base.html``.

    The map is what motivated the ticket, but the map IS ``/`` (``public:map``
    permanently redirects there), so asserting it separately would just be
    the home-page test twice. A second, unrelated page is the assertion that
    actually says something: the panel follows the base template rather than
    one view's context.
    """
    response = signed_in.get(reverse("public:colophon"))

    assert response.status_code == 200
    assert 'data-testid="debug-log-panel"' in response.content.decode()


@pytest.mark.django_db
@override_flag("debug_log", active=True)
def test_recording_is_not_switched_on_by_the_flag(signed_in: Client) -> None:
    """Holding the flag must not start a trace.

    The checkbox ships unchecked and recording is a per-device choice made
    in the panel (persisted client-side). If the server could pre-check it,
    every GRP_DEBUG member would silently pay for a trace on every page.
    """
    response = signed_in.get(reverse("public:home"))

    body = response.content.decode()
    assert 'id="debug-log-enabled"' in body
    # No `checked` attribute anywhere in the toggle's own input.
    toggle = body[body.index('id="debug-log-enabled"') :][:200]
    assert "checked" not in toggle


@pytest.mark.django_db
def test_anonymous_visitors_pay_no_query_for_the_gate(client: Client) -> None:
    """The homepage's anonymous path must be unchanged by this flag.

    ``docs/feature-flags.md`` records SNOW-749 deleting a ``download_sync``
    flag before merge precisely because its gate cost the homepage three
    queries. This flag gates a surface on ``base.html``, so it is read on
    that same page — and has to answer that objection rather than repeat it.

    The first draft did not: it read the flag for everyone, and
    ``monitor_query_counts`` caught ``home`` at 5 -> 8 in CI. The context
    processor now evaluates ``request.user.is_authenticated`` first and
    stops there for a visitor with no session.

    **No cache warming here, deliberately.** An earlier version of this test
    made a throwaway request first, which warmed waffle's flag cache and
    hid the regression entirely — the very failure this now pins. The first
    request a process serves is the honest measurement, and in production
    waffle's cache is ``DatabaseCache`` anyway, so a warm cache trades three
    model queries for a cache-table one rather than for none.
    """
    _install_group_scoped_flag()

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("public:home"))

    assert response.status_code == 200
    waffle_queries = [q for q in ctx.captured_queries if "waffle" in q["sql"]]
    assert waffle_queries == []


@pytest.mark.django_db
def test_everyone_yes_does_not_reach_an_anonymous_visitor(client: Client) -> None:
    """The stated cost of the anonymous short-circuit, pinned.

    Reading the flag as "never active for an anonymous request" is true of
    the group scoping this flag ships with, but not of an ``everyone = Yes``
    an operator could set in the admin. That combination is deliberately
    unsupported — turning THIS flag on for everyone would ship a debug panel
    to every visitor, which is a mistake in its own right — but it must be a
    contract someone can read, not a surprise found later in a bug report.
    """
    flag = Flag.objects.create(name="debug_log", note="SNOW-812 debug trace")
    flag.everyone = True
    flag.save()

    response = client.get(reverse("public:home"))

    assert response.status_code == 200
    assert 'data-testid="debug-log-panel"' not in response.content.decode()


@pytest.mark.django_db
def test_signed_in_cost_is_one_query(client: Client) -> None:
    """A signed-in request pays exactly one extra query, and no more.

    That query is the ``auth_user_groups`` join a group-scoped flag needs
    and that nothing caches. It is the whole cost of the gate, it is
    quoted in ``docs/feature-flags.md``, and pinning it here is what stops
    a later change to the context processor — dropping the
    ``SimpleLazyObject``, say — from quietly turning one query into four.
    """
    group = _install_group_scoped_flag()
    user = User.objects.create_user("debugger", password="unused-in-this-test")
    user.groups.add(group)
    client.force_login(user)

    # Warmed on purpose here, unlike the anonymous test above: this
    # assertion is about the ONE query that survives a warm cache — the
    # group join nothing caches — rather than about the cold-start cost.
    client.get(reverse("public:home"))
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("public:home"))

    assert response.status_code == 200
    assert 'data-testid="debug-log-panel"' in response.content.decode()
    group_queries = [q for q in ctx.captured_queries if "auth_user_groups" in q["sql"]]
    assert len(group_queries) == 1


def _install_group_scoped_flag() -> Group:
    """Create the ``debug_log`` flag scoped to ``GRP_DEBUG``, as shipped.

    ``override_flag`` cannot express group scoping — it forces the answer
    rather than exercising the targeting — and the targeting is precisely
    what the two query-cost tests above measure.

    Returns:
        The ``GRP_DEBUG`` group, for adding a user to.

    """
    group = Group.objects.create(name="GRP_DEBUG")
    # cast: see sync_waffle_flags.py — ``Flag.objects`` is annotated against
    # ``AbstractBaseFlag``, which does not declare the ``groups`` M2M.
    flag = cast(
        Flag, Flag.objects.create(name="debug_log", note="SNOW-812 debug trace")
    )
    flag.groups.set([group])
    return group
