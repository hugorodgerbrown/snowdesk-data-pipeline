"""
tests/public/test_banners.py — the admin-managed site banners.

Covers both halves of the read side:

  - ``apps.public.banners`` — the level-to-token mapping, whether a
    reader's dismissal can be recorded, the targeting and ordering of
    ``banners_for_request``, and its per-request cache.
  - the rendered strip on a real page — that a banner authored in the
    admin reaches every page extending ``public/base.html``, paints in its
    level's palette, escapes its own content unless the author explicitly
    allowed markup, and disappears once dismissed.

``PersistentMessage`` is a third-party model, so there is no factory for
it — rows are created directly, which is also how an admin creates them.
"""

from __future__ import annotations

import datetime
from contextlib import AbstractContextManager
from typing import Callable

import pytest
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser, Group, User
from django.http import HttpRequest
from django.test import Client, RequestFactory
from django.urls import reverse
from persistent_messages.models import PersistentMessage

from apps.public.banners import (
    DEFAULT_KIND,
    banners_for_request,
    dismiss_url_for,
    kind_for,
)
from tests.factories import UserFactory


def make_banner(**kwargs: object) -> PersistentMessage:
    """
    Create one banner row, defaulting to "visible to everyone".

    Args:
        **kwargs: Field overrides passed through to the model.

    Returns:
        The saved row.

    """
    fields: dict[str, object] = {
        "content": "Scheduled maintenance on Sunday.",
        "level": messages.WARNING,
        "target": PersistentMessage.TargetType.ALL_USERS,
    }
    fields.update(kwargs)
    return PersistentMessage.objects.create(**fields)


def request_for(user: User | AnonymousUser, path: str = "/") -> HttpRequest:
    """
    Build a request carrying ``user``, as AuthenticationMiddleware would.

    Args:
        user: The user (or ``AnonymousUser``) to attach.
        path: The request path.

    Returns:
        The request.

    """
    request = RequestFactory().get(path)
    request.user = user
    return request


class TestKindFor:
    """The level-to-status-token mapping."""

    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            (messages.DEBUG, "info"),
            (messages.INFO, "info"),
            (messages.SUCCESS, "success"),
            (messages.WARNING, "warning"),
            (messages.ERROR, "error"),
        ],
    )
    def test_every_django_level_maps_to_a_real_token(
        self, level: int, expected: str
    ) -> None:
        """All five levels land on one of the four status palettes.

        ``debug`` has no palette of its own and shares ``info``: four
        ``bg-status-*`` families exist in ``src/css/main.css``, and a name
        outside them paints an unstyled strip.
        """
        banner = PersistentMessage(content="…", level=level)

        assert kind_for(banner) == expected

    def test_an_unknown_level_falls_back_to_the_calmest_palette(self) -> None:
        """A level with no tag must not be able to dress itself as an error.

        A ``MESSAGE_TAGS`` override, or a level integer no tag covers,
        reaches ``kind_for`` with a ``level_tag`` we do not know. Painting
        that as ``error`` would let a configuration slip shout at readers.
        """
        banner = PersistentMessage(content="…", level=99)

        assert kind_for(banner) == DEFAULT_KIND == "info"


@pytest.mark.django_db
class TestDismissUrlFor:
    """Whether this reader's dismissal can be recorded."""

    def test_a_signed_in_reader_gets_the_url(self) -> None:
        """The package's own dismissal endpoint, for a dismissable row."""
        user = UserFactory.create()
        banner = make_banner(is_dismissable=True)

        assert dismiss_url_for(banner, request_for(user)) == reverse(
            "persistent_messages:dismiss_message", args=[banner.id]
        )

    def test_an_anonymous_reader_gets_nothing(self) -> None:
        """The endpoint is ``login_required``, so the URL would only redirect.

        Rendering it anyway would put a DELETE on the page that can never
        succeed; ``static/js/persistent_messages.js`` reads the attribute's
        presence as "this can be recorded", so an empty string here is what
        keeps it from firing.
        """
        banner = make_banner(is_dismissable=True)

        assert dismiss_url_for(banner, request_for(AnonymousUser())) == ""

    def test_an_undismissable_row_gets_nothing_even_when_signed_in(self) -> None:
        """The row's own flag still wins — the package returns "" for it."""
        user = UserFactory.create()
        banner = make_banner(is_dismissable=False)

        assert dismiss_url_for(banner, request_for(user)) == ""


@pytest.mark.django_db
class TestBannersForRequest:
    """Which banners apply, and in what order."""

    def test_an_all_users_banner_reaches_an_anonymous_reader(self) -> None:
        """ALL_USERS is the target a site-wide notice uses."""
        banner = make_banner()

        assert banners_for_request(request_for(AnonymousUser())) == [banner]

    def test_an_authenticated_only_banner_does_not(self) -> None:
        """AUTHENTICATED_USERS is invisible to a logged-out visitor."""
        make_banner(target=PersistentMessage.TargetType.AUTHENTICATED_ONLY)

        assert banners_for_request(request_for(AnonymousUser())) == []

    def test_a_group_targeted_banner_reaches_only_that_group(self) -> None:
        """USERS_OR_GROUPS with a group is how an ops notice is scoped."""
        group = Group.objects.create(name="ops")
        member = UserFactory.create()
        member.groups.add(group)
        outsider = UserFactory.create()
        banner = make_banner(target=PersistentMessage.TargetType.USERS_OR_GROUPS)
        banner.target_groups.add(group)

        assert banners_for_request(request_for(member)) == [banner]
        assert banners_for_request(request_for(outsider)) == []

    def test_an_expired_banner_is_gone(self) -> None:
        """``display_until`` in the past is how a notice retires itself.

        This is the property the hard-coded off-season bar never had: its
        copy could only be corrected by a deploy, and it outlived the claim
        it was making. A dated row expires on its own.
        """
        make_banner(
            display_until=datetime.datetime.now(tz=datetime.UTC)
            - datetime.timedelta(minutes=1)
        )

        assert banners_for_request(request_for(AnonymousUser())) == []

    def test_a_future_banner_is_not_shown_yet(self) -> None:
        """``display_from`` lets an operator schedule a notice ahead."""
        make_banner(
            display_from=datetime.datetime.now(tz=datetime.UTC)
            + datetime.timedelta(hours=1)
        )

        assert banners_for_request(request_for(AnonymousUser())) == []

    def test_a_dismissed_banner_is_gone_for_that_reader_only(self) -> None:
        """Dismissal is per-account, which is the point of storing it."""
        reader = UserFactory.create()
        other = UserFactory.create()
        banner = make_banner()
        banner.dismiss(reader)

        assert banners_for_request(request_for(reader)) == []
        assert banners_for_request(request_for(other)) == [banner]

    def test_the_most_severe_banner_comes_first(self) -> None:
        """Order is severity, then newest — a warning outranks a note.

        Two banners at once is unusual but legal, and the reader should hit
        the maintenance warning before the marketing line.
        """
        info = make_banner(content="Now readable offline.", level=messages.INFO)
        error = make_banner(content="Ingest is down.", level=messages.ERROR)

        assert banners_for_request(request_for(AnonymousUser())) == [error, info]

    def test_the_result_is_cached_on_the_request(
        self, django_assert_num_queries: Callable[[int], AbstractContextManager[None]]
    ) -> None:
        """A second call in the same request costs no second query.

        The package's own shortcut memoises this with ``functools.cache``
        keyed on the request, which retains every request for the life of
        the process; the cache belongs on the request instead, and this is
        the assertion that it is there.
        """
        make_banner()
        request = request_for(AnonymousUser())

        first = banners_for_request(request)
        with django_assert_num_queries(0):
            second = banners_for_request(request)

        assert second is first

    def test_a_separate_request_is_not_served_the_first_one_cache(self) -> None:
        """The cache is per request, not process-wide.

        The failure this rules out is the one the package's own decorator
        has: a banner created (or dismissed) between two requests must be
        reflected in the second.
        """
        first_request = request_for(AnonymousUser())
        assert banners_for_request(first_request) == []

        banner = make_banner()

        assert banners_for_request(request_for(AnonymousUser())) == [banner]


@pytest.mark.django_db
class TestRenderedOnThePage:
    """The strip as a reader receives it."""

    def test_a_banner_reaches_the_homepage(self) -> None:
        """Authored in the admin, rendered with no view change.

        The context processor puts it on every page extending
        ``public/base.html``, so this asserts the wiring end to end rather
        than one view's context.
        """
        banner = make_banner(content="Scheduled maintenance on Sunday.")

        content = Client().get(reverse("public:home")).content.decode()

        assert f'id="pmid-{banner.id}"' in content
        assert "Scheduled maintenance on Sunday." in content

    def test_it_paints_in_its_level_palette(self) -> None:
        """The row's level picks the status tokens, not a hard-coded colour."""
        make_banner(level=messages.ERROR)

        content = Client().get(reverse("public:home")).content.decode()

        assert "bg-status-error-bg" in content
        assert "text-status-error-text" in content

    def test_nothing_renders_when_no_banner_is_active(self) -> None:
        """The normal state. No empty strip, no stray border."""
        content = Client().get(reverse("public:home")).content.decode()

        assert 'id="pmid-' not in content

    def test_content_is_escaped_unless_the_author_allowed_markup(self) -> None:
        """The default is escaped, and that default is the safe one.

        ``mark_content_safe`` is the one switch that lets HTML through, it
        is reachable only by a staff user with change permission on the
        model, and leaving it off must mean the angle brackets are shown
        rather than executed.
        """
        make_banner(content='<script>alert(1)</script> <a href="#">link</a>')

        content = Client().get(reverse("public:home")).content.decode()

        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
        assert "<script>alert(1)</script>" not in content

    def test_markup_survives_when_the_author_did_allow_it(self) -> None:
        """A banner's whole job may be to carry one link."""
        make_banner(
            content='Read the <a href="/help/">notes</a>.', mark_content_safe=True
        )

        content = Client().get(reverse("public:home")).content.decode()

        assert '<a href="/help/">notes</a>' in content

    def test_an_undismissable_banner_renders_no_dismiss_control(self) -> None:
        """No "×", and none of the hooks that would drive one."""
        banner = make_banner(is_dismissable=False)

        content = Client().get(reverse("public:home")).content.decode()
        opening_tag = content[
            content.index(f'id="pmid-{banner.id}"') : content.index(
                ">", content.index(f'id="pmid-{banner.id}"')
            )
        ]

        assert "data-overlay" not in opening_tag
        assert "data-dismiss-url" not in opening_tag

    def test_an_anonymous_reader_gets_no_dismiss_url(self) -> None:
        """The × still hides the strip; nothing claims it can be recorded."""
        banner = make_banner(is_dismissable=True)

        content = Client().get(reverse("public:home")).content.decode()
        start = content.index(f'id="pmid-{banner.id}"')
        opening_tag = content[start : content.index(">", start)]

        assert 'data-overlay-hide="class"' in opening_tag
        assert "data-dismiss-url" not in opening_tag

    def test_a_signed_in_reader_gets_the_dismiss_url(self) -> None:
        """The attribute static/js/persistent_messages.js DELETEs."""
        user = UserFactory.create()
        banner = make_banner(is_dismissable=True)
        client = Client()
        client.force_login(user)

        content = client.get(reverse("public:home")).content.decode()

        assert (
            f'data-dismiss-url="{reverse("persistent_messages:dismiss_message", args=[banner.id])}"'
            in content
        )

    def test_dismissing_it_stops_it_coming_back(self) -> None:
        """The whole reason the dismissal is stored rather than local.

        This is the gap the removed off-season bar had in the other
        direction: its note lived in the first-run panel, which a returning
        reader never saw again. Here the banner keeps showing until the
        reader closes it, and then stops.
        """
        user = UserFactory.create()
        banner = make_banner(is_dismissable=True)
        client = Client()
        client.force_login(user)

        before = client.get(reverse("public:home")).content.decode()
        response = client.delete(
            reverse("persistent_messages:dismiss_message", args=[banner.id])
        )
        after = client.get(reverse("public:home")).content.decode()

        assert f'id="pmid-{banner.id}"' in before
        assert response.status_code == 204
        assert f'id="pmid-{banner.id}"' not in after

    def test_the_dismissal_endpoint_turns_an_anonymous_reader_away(self) -> None:
        """``login_required``, which is why the URL is never rendered for them."""
        banner = make_banner(is_dismissable=True)

        response = Client().delete(
            reverse("persistent_messages:dismiss_message", args=[banner.id])
        )

        assert response.status_code == 302
