"""Tests for apps.core.views — the /livez and /healthz health checks (SNOW-565)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import OperationalError
from django.test import Client, override_settings

from tests.factories import AccountFactory


def test_livez_returns_ok() -> None:
    """The liveness probe answers 200 with a bare ``ok`` body."""
    response = Client().get("/livez")
    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.django_db
def test_livez_issues_no_queries(django_assert_num_queries: Any) -> None:
    """The liveness probe touches the database zero times.

    This is the regression test for the whole design: if a future middleware
    or decorator reads ``request.user``, the session lookup would make the
    probe database-dependent and Render would start replacing instances over
    a Postgres blip.
    """
    with django_assert_num_queries(0):
        response = Client().get("/livez")
    assert response.status_code == 200


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_livez_issues_no_queries_for_a_request_carrying_a_session(
    django_assert_num_queries: Any,
) -> None:
    """SNOW-565: the ``_POSTHOG_EXEMPT_PATHS`` entry keeps ``/livez`` query-free.

    The anonymous case above cannot catch a regression here: with no session
    cookie, ``request.user`` resolves to AnonymousUser without touching the
    database, so the probe reads zero queries whether or not the exemption
    exists. Sending a real session cookie is what gives the assertion teeth
    — with analytics enabled, PosthogContextMiddleware reads ``request.user``
    to tag identities, and that access loads the session and the user row.
    Remove ``/livez`` from ``_POSTHOG_EXEMPT_PATHS`` and this test fails.
    """
    client = Client()
    client.force_login(AccountFactory.create().user)
    with django_assert_num_queries(0):
        response = client.get("/livez")
    assert response.status_code == 200


@pytest.mark.django_db
def test_healthz_returns_ok_with_a_reachable_database() -> None:
    """The readiness probe answers 200 when ``auth_user`` can be read."""
    AccountFactory.create()
    response = Client().get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_healthz_is_healthy_on_an_empty_user_table() -> None:
    """No rows in ``auth_user`` is a healthy database, not a fault.

    The probe asserts that the query completes, not that it returns a row —
    a freshly-migrated database has no users and is perfectly serviceable.
    """
    response = Client().get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_healthz_returns_503_when_the_database_is_unreachable() -> None:
    """A database failure is reported as 503 with no detail in the body."""
    with patch(
        "apps.core.views.User.objects.exists",
        side_effect=OperationalError("could not connect to server at db:5432"),
    ):
        response = Client().get("/healthz")
    assert response.status_code == 503
    assert response.content == b"error"


@pytest.mark.django_db
def test_healthz_does_not_leak_the_database_error() -> None:
    """The driver's error string never reaches the response body.

    Connection errors routinely carry the host, port, and role of the
    database; the endpoint is public, so the body is a fixed word.
    """
    with patch(
        "apps.core.views.User.objects.exists",
        side_effect=OperationalError("FATAL: role 'snowdesk_prod' does not exist"),
    ):
        response = Client().get("/healthz")
    assert b"snowdesk_prod" not in response.content
    assert b"FATAL" not in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/livez", "/healthz"])
def test_health_checks_are_never_cached(path: str) -> None:
    """Neither probe may be served from a cache — no-store on both."""
    response = Client().get(path)
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/livez", "/healthz"])
def test_health_checks_resolve_without_a_trailing_slash(path: str) -> None:
    """The probes answer directly, not via an APPEND_SLASH redirect.

    Registered ahead of the ``<region_id:region_id>/`` catch-all in
    apps.public.urls, so ``/healthz`` must not be redirected to
    ``/healthz/`` and resolved as a region.
    """
    response = Client().get(path)
    assert response.status_code == 200


@pytest.mark.django_db
def test_every_declared_health_check_path_is_routed() -> None:
    """``settings.HEALTH_CHECK_PATHS`` and the URLconf cannot drift apart.

    The constant drives the PostHog exemption and the production
    SSL-redirect exemption, so a path listed there but not routed would
    silently configure exemptions for a URL that 404s.
    """
    assert settings.HEALTH_CHECK_PATHS, "no health-check paths declared"
    for path in settings.HEALTH_CHECK_PATHS:
        assert Client().get(path).status_code == 200, f"{path} is not routed"


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/livez", "/healthz"])
def test_health_checks_are_exempt_from_the_ssl_redirect(path: str) -> None:
    """SNOW-565: an HTTP probe gets a 200, not a 301, under SECURE_SSL_REDIRECT.

    Render's prober reaches the instance behind the TLS-terminating proxy,
    so it sends no ``X-Forwarded-Proto`` and SecurityMiddleware would answer
    the probe with a redirect that Render scores as a failed health check.
    The patterns are rebuilt here exactly as production.py derives them, so
    a change to the path shape is caught by the regex, not just the literal.
    """
    exempt = [rf"^{p.lstrip('/')}$" for p in settings.HEALTH_CHECK_PATHS]
    with override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=exempt):
        assert Client().get(path).status_code == 200
        # Control: a normal page under the same settings still redirects,
        # proving the exemption is what produced the 200 above.
        assert Client().get("/").status_code == 301
