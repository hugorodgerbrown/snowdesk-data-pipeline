"""
tests/accounts/test_accounts_tags.py — the ``{% sign_in_href %}`` template tag
(SNOW-826).

The tag builds the sign-in URL with the current page hung off it as
``?next=``, which is what lets a visitor who signs in from a button land back
where they were rather than on the map. Three templates render it — the nav
and the two trips save buttons — so a mistake here is site-wide rather than
local, and the encoding is the part that can go wrong quietly: a path whose
query string is not encoded arrives at the sign-in view as extra parameters
of ITS OWN query string, and the destination silently loses its tail.

``safe="/"`` is asserted directly rather than inferred. It reproduces the
``urlencode`` template filter the three call sites used before this ticket,
so a change to it would rewrite every href the site emits while every
higher-level test carried on passing.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.templatetags.accounts_tags import sign_in_href

TEMPLATE = Template("{% load accounts_tags %}{% sign_in_href %}")


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory."""
    return RequestFactory()


class TestSignInHref:
    """The string the tag returns for one request."""

    def test_a_bare_path_is_appended_as_next(self, rf: RequestFactory) -> None:
        """The common case: a page with no query string of its own."""
        request = rf.get("/trips/s/abc123/")

        href = sign_in_href(Context({"request": request}))

        assert href == f"{reverse('accounts:sign_in')}?next=/trips/s/abc123/"

    def test_a_query_string_is_encoded_but_the_slashes_are_not(
        self, rf: RequestFactory
    ) -> None:
        """``?`` and ``&`` are encoded; ``/`` is left alone by ``safe="/"``.

        An unencoded ``?`` would end the ``next`` value at the first
        character of the destination's own query string, and an unencoded
        ``&`` would split the rest into parameters of the sign-in URL.
        """
        request = rf.get("/", {"d": "2026-04-08", "panel": "routes"})

        href = sign_in_href(Context({"request": request}))

        assert href == (
            f"{reverse('accounts:sign_in')}?next=/%3Fd%3D2026-04-08%26panel%3Droutes"
        )
        assert "%2F" not in href

    def test_no_request_falls_back_to_the_bare_sign_in_url(self) -> None:
        """The nav renders on every page, so this must not raise.

        A context with no request — rendering a partial outside the request
        cycle — yields a working sign-in link without the return trip,
        rather than a template error taking down the whole page.
        """
        assert sign_in_href(Context({})) == reverse("accounts:sign_in")

    def test_the_tag_is_loadable_and_renders(self, rf: RequestFactory) -> None:
        """Through the template engine, as the three call sites reach it."""
        request = rf.get("/trips/")

        rendered = TEMPLATE.render(Context({"request": request}))

        assert rendered == f"{reverse('accounts:sign_in')}?next=/trips/"
