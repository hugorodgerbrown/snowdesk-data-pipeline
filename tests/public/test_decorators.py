"""
tests/public/test_decorators.py — Unit tests for public.decorators.

Covers the ``lowercase_region_id`` decorator:

  - Pass-through when the ``region_id`` kwarg is already lowercase.
  - 301 redirect when the ``region_id`` kwarg contains uppercase characters.
  - Query string is preserved on the redirect.
  - Only the first occurrence of ``region_id`` in the path is replaced,
    so a path like ``/CH-4115/some-slug/CH-4115-detail/`` rewrites only
    the first segment.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect

from public.decorators import lowercase_region_id


def _make_request(path: str, query_string: str = "") -> HttpRequest:
    """Build a minimal HttpRequest with the given path and query string."""
    request = HttpRequest()
    request.method = "GET"
    request.path = path
    request.META["QUERY_STRING"] = query_string
    return request


def _dummy_view(
    request: HttpRequest, region_id: str = "", **kwargs: object
) -> HttpResponse:
    """Trivial view that echoes the region_id in the response body."""
    return HttpResponse(f"ok:{region_id}")


class TestLowercaseRegionId:
    """Unit tests for the ``lowercase_region_id`` decorator."""

    def test_passes_through_when_already_lowercase(self) -> None:
        """When region_id is already lowercase the wrapped view is called."""
        decorated = lowercase_region_id(_dummy_view)
        request = _make_request("/ch-4115/valais/2025-03-15/")
        response = decorated(request, region_id="ch-4115")
        assert response.status_code == 200
        assert b"ok:ch-4115" in response.content

    def test_redirects_uppercase_to_lowercase(self) -> None:
        """An uppercase region_id triggers a 301 redirect to the lowercase path."""
        decorated = lowercase_region_id(_dummy_view)
        request = _make_request("/CH-4115/valais/2025-03-15/")
        response = decorated(request, region_id="CH-4115")
        assert isinstance(response, HttpResponsePermanentRedirect)
        assert response.status_code == 301
        assert response["Location"] == "/ch-4115/valais/2025-03-15/"

    def test_preserves_query_string_on_redirect(self) -> None:
        """The query string is preserved when redirecting to the lowercase path."""
        decorated = lowercase_region_id(_dummy_view)
        request = _make_request("/CH-4115/valais/2025-03-15/", query_string="issue=abc")
        response = decorated(request, region_id="CH-4115")
        assert isinstance(response, HttpResponsePermanentRedirect)
        assert response["Location"] == "/ch-4115/valais/2025-03-15/?issue=abc"

    def test_only_replaces_first_occurrence(self) -> None:
        """Only the first occurrence of region_id in the path is replaced."""
        decorated = lowercase_region_id(_dummy_view)
        # Contrived path where the region_id string also appears later.
        request = _make_request("/CH-4115/some-slug/CH-4115-detail/")
        response = decorated(request, region_id="CH-4115")
        assert isinstance(response, HttpResponsePermanentRedirect)
        # First occurrence replaced; second preserved.
        assert response["Location"] == "/ch-4115/some-slug/CH-4115-detail/"

    def test_mixed_case_at_region_id_prefix(self) -> None:
        """Mixed-case prefix (at-) is also redirected."""
        decorated = lowercase_region_id(_dummy_view)
        request = _make_request("/AT-02-01/")
        response = decorated(request, region_id="AT-02-01")
        assert isinstance(response, HttpResponsePermanentRedirect)
        assert response["Location"] == "/at-02-01/"

    def test_empty_query_string_not_appended(self) -> None:
        """When there is no query string the redirect URL has no trailing ``?``."""
        decorated = lowercase_region_id(_dummy_view)
        request = _make_request("/CH-4115/", query_string="")
        response = decorated(request, region_id="CH-4115")
        assert isinstance(response, HttpResponsePermanentRedirect)
        assert "?" not in response["Location"]

    def test_functools_wraps_preserves_name(self) -> None:
        """The decorator preserves the wrapped function's ``__name__``."""
        decorated = lowercase_region_id(_dummy_view)
        assert decorated.__name__ == "_dummy_view"

    @pytest.mark.parametrize(
        "region_id",
        ["ch-4115", "at-02-01", "fr-52", "it-32-bz-01"],
    )
    def test_lowercase_passthrough_parametrised(self, region_id: str) -> None:
        """A variety of lowercase IDs all pass through without redirect."""
        decorated = lowercase_region_id(_dummy_view)
        path = f"/{region_id}/"
        request = _make_request(path)
        response = decorated(request, region_id=region_id)
        assert response.status_code == 200
