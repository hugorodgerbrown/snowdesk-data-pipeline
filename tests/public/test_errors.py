"""
tests/public/test_errors.py — Tests for apps/public/errors.py (SNOW-1151).

Covers:
  - The handlers are wired in config.urls and CSRF_FAILURE_VIEW.
  - 404 / 403 / 400 / 500 / CSRF failure through the real middleware stack,
    each in its three negotiated shapes: HTML, JSON and HTMX plain text.
  - 403 and 404 render the full-site page (nav included); 400 and 500
    render the standalone shell (no nav account menu).
  - 500 and 400 render with no database access: those tests carry no
    django_db mark, so pytest-django fails any query.
  - Fallbacks: a failing full-site render drops to the standalone shell,
    and a failing standalone render drops to the literal HTML.
  - The staff-only /_errors/<kind>/ preview.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from django.conf import settings
from django.test import Client, RequestFactory

from apps.public import errors
from config import urls as root_urls
from tests.factories import UserFactory

ERROR_URLS = "tests.public._error_urls"


@pytest.fixture(autouse=True)
def _debug_off(settings: Any) -> None:
    """Run with DEBUG off: with it on, Django shows its technical pages."""
    settings.DEBUG = False


HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}
JSON: dict[str, Any] = {"HTTP_ACCEPT": "application/json"}


def test_handlers_are_wired() -> None:
    assert root_urls.handler400 == "apps.public.errors.bad_request"
    assert root_urls.handler403 == "apps.public.errors.permission_denied"
    assert root_urls.handler404 == "apps.public.errors.page_not_found"
    assert root_urls.handler500 == "apps.public.errors.server_error"
    assert settings.CSRF_FAILURE_VIEW == "apps.public.errors.csrf_failure"


class TestWantsJson:
    @pytest.mark.parametrize(
        "path", ["/api/nope/", "/oauth/nope/", "/.well-known/nope"]
    )
    def test_json_paths(self, path: str) -> None:
        assert errors.wants_json(RequestFactory().get(path))

    def test_json_accept_header(self) -> None:
        assert errors.wants_json(RequestFactory().get("/x/", **JSON))

    def test_browser_accept_is_html(self) -> None:
        request = RequestFactory().get(
            "/x/", HTTP_ACCEPT="text/html,application/xhtml+xml,*/*;q=0.8"
        )
        assert not errors.wants_json(request)

    def test_wildcard_accept_is_html(self) -> None:
        assert not errors.wants_json(RequestFactory().get("/x/", HTTP_ACCEPT="*/*"))


@pytest.mark.django_db
class TestNotFound:
    def test_html_is_full_site_page(self, client: Client) -> None:
        response = client.get("/no-such-page/at-all/")
        assert response.status_code == 404
        body = response.content.decode()
        assert "Page not found" in body
        assert "<nav" in body
        assert 'href="/map/"' in body

    def test_api_path_is_json(self, client: Client) -> None:
        response = client.get("/api/no-such-endpoint/")
        assert response.status_code == 404
        assert response.json() == {"error": "not_found", "status": 404}

    def test_htmx_is_plain_text(self, client: Client) -> None:
        response = client.get("/no-such-page/at-all/", **HTMX)
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/plain")
        assert response.content.decode() == "Page not found"

    def test_render_failure_falls_back_to_standalone(self, client: Client) -> None:
        with mock.patch.object(errors, "render", side_effect=RuntimeError):
            response = client.get("/no-such-page/at-all/")
        assert response.status_code == 404
        assert "Page not found" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.urls(ERROR_URLS)
class TestThroughMiddleware:
    def test_permission_denied(self, client: Client) -> None:
        response = client.get("/_test/denied/")
        assert response.status_code == 403
        body = response.content.decode()
        assert "You don&#x27;t have access to this page" in body
        assert "not yours" not in body

    def test_permission_denied_json(self, client: Client) -> None:
        response = client.get("/_test/denied/", **JSON)
        assert response.json() == {"error": "forbidden", "status": 403}

    def test_suspicious_is_standalone_400(self, client: Client) -> None:
        response = client.get("/_test/suspicious/")
        assert response.status_code == 400
        body = response.content.decode()
        assert "couldn&#x27;t be read" in body
        assert "Open the map" not in body

    def test_disallowed_host_is_standalone_400(
        self, client: Client, settings: Any
    ) -> None:
        settings.ALLOWED_HOSTS = ["testserver"]
        response = client.get("/", HTTP_HOST="evil.example")
        assert response.status_code == 400
        assert "couldn&#x27;t be read" in response.content.decode()

    def test_server_error_html(self) -> None:
        client = Client(raise_request_exception=False)
        response = client.get("/_test/boom/")
        assert response.status_code == 500
        body = response.content.decode()
        assert "Something went wrong on our side" in body
        assert "boom" not in body

    def test_server_error_json(self) -> None:
        client = Client(raise_request_exception=False)
        response = client.get("/api/_test/boom/")
        assert response.status_code == 500
        assert response.json() == {"error": "server_error", "status": 500}

    def test_server_error_htmx(self) -> None:
        client = Client(raise_request_exception=False)
        response = client.get("/_test/boom/", **HTMX)
        assert response.status_code == 500
        assert response.content.decode() == "Something went wrong on our side"

    def test_csrf_failure(self) -> None:
        client = Client(enforce_csrf_checks=True)
        response = client.post("/_test/form/", {"a": "b"})
        assert response.status_code == 403
        assert "This form has expired" in response.content.decode()

    def test_csrf_failure_htmx(self) -> None:
        client = Client(enforce_csrf_checks=True)
        response = client.post("/_test/form/", {"a": "b"}, **HTMX)
        assert response.status_code == 403
        assert response.content.decode() == "This form has expired"


class TestContextFree:
    """No django_db mark: any query in these renders fails the test."""

    def test_server_error_renders_without_database(self) -> None:
        response = errors.server_error(RequestFactory().get("/x/"))
        assert response.status_code == 500
        body = response.content.decode()
        assert body.lstrip().startswith("<!DOCTYPE html>")
        assert "Something went wrong on our side" in body
        assert "css/output.css" in body

    def test_bad_request_renders_without_database(self) -> None:
        response = errors.bad_request(RequestFactory().get("/x/"), ValueError())
        assert response.status_code == 400

    def test_standalone_failure_returns_literal_fallback(self) -> None:
        with mock.patch.object(
            errors, "render_to_string", side_effect=ValueError("no manifest")
        ):
            response = errors.server_error(RequestFactory().get("/x/"))
        assert response.status_code == 500
        assert response.content.decode() == errors._FALLBACK_HTML


@pytest.mark.django_db
class TestPreview:
    def test_anonymous_is_redirected_to_login(self, client: Client) -> None:
        response = client.get("/_errors/404/")
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    @pytest.mark.parametrize("kind", sorted(errors.ERROR_PAGES))
    def test_staff_sees_each_page_with_its_status(
        self, client: Client, kind: str
    ) -> None:
        client.force_login(UserFactory.create(is_staff=True))
        response = client.get(f"/_errors/{kind}/")
        page = errors.ERROR_PAGES[kind]
        assert response.status_code == page.status
        assert str(page.heading) in response.content.decode().replace("&#x27;", "'")

    def test_unknown_kind_is_404(self, client: Client) -> None:
        client.force_login(UserFactory.create(is_staff=True))
        response = client.get("/_errors/418/")
        assert response.status_code == 404
        assert "Page not found" in response.content.decode()
