"""
tests/accounts/test_sign_in_next.py — the post-sign-in ``next`` destination
(SNOW-825).

Two things are under test and only one of them is the feature.

THE OPEN-REDIRECT CASES ARE THE POINT. ``next`` is attacker-supplied on
every path that reads it, and a sign-in page that redirects wherever it is
told is a phishing instrument wearing Snowdesk's domain. Each hostile shape
gets its own test — an absolute foreign URL, the protocol-relative
``//evil.example``, and the two backslash variants browsers normalise into
it — so a regression names the shape it let through.

THE THREE PATHS. Password, magic link and passkey each honour a safe
``next`` and each fall back to the destination they had before this ticket
when ``next`` is absent OR unsafe:

  password   → ``next``, else the map (``public:home``).
  magic link → the emailed URL carries an encoded ``?next=``, ``account_view``
               carries it across its confirm POST, and the redirect after
               login honours it, else ``/?panel=favourites``.
  passkey    → the JSON response echoes a validated ``next`` (or null) for
               the browser to navigate to, else the browser's own /account/.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import Client, RequestFactory
from django.urls import reverse
from pytest_django.fixtures import Settings

from apps.accounts.redirects import safe_next
from apps.accounts.services.token import SALT_ACCOUNT_ACCESS, generate_token
from tests.factories import AccountFactory, UserFactory

_STRONG = "Str0ngPassw0rd!"

# A same-site destination, and the shape the trip CTAs actually send.
_SAFE_NEXT = "/trips/s/abcdef123456/"

# Where each path lands with no usable ``next``.
_HOME = "/"
_VERIFIED_LANDING_URL = "/?panel=favourites"


@pytest.fixture(autouse=True)
def _locmem_email(settings: Settings) -> None:
    """Use the locmem email backend so the magic-link path does not hit SMTP."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def _set_auth_challenge(client: Client, challenge: str) -> None:
    """Seed the WebAuthn authentication challenge into the client's session."""
    session = client.session
    session["webauthn_auth_challenge"] = challenge
    session.save()


# ---------------------------------------------------------------------------
# safe_next — the safety helper
# ---------------------------------------------------------------------------


class TestSafeNext:
    """Unit tests for ``apps.accounts.redirects.safe_next``."""

    def _request(self, *, secure: bool = False) -> Any:
        """Return a request on the default test host, optionally over HTTPS."""
        return RequestFactory().get("/sign-in/", secure=secure)

    def test_same_host_relative_path_is_accepted(self) -> None:
        """The whole point: a path on this site comes back unchanged."""
        assert safe_next(self._request(), _SAFE_NEXT) == _SAFE_NEXT

    def test_absolute_external_url_is_rejected(self) -> None:
        """The phishing case — a foreign host must never be redirected to."""
        assert safe_next(self._request(), "https://evil.example/x") is None

    def test_protocol_relative_url_is_rejected(self) -> None:
        """``//host`` inherits the current scheme and leaves the site."""
        assert safe_next(self._request(), "//evil.example") is None

    def test_backslash_prefixed_url_is_rejected(self) -> None:
        r"""Browsers normalise ``/\`` to ``//`` — so it is the same attack."""
        assert safe_next(self._request(), "/\\evil.example") is None

    def test_double_backslash_url_is_rejected(self) -> None:
        """The other half of the same normalisation."""
        assert safe_next(self._request(), "\\\\evil.example") is None

    def test_empty_and_missing_are_rejected(self) -> None:
        """Nothing supplied means the caller uses its own default."""
        assert safe_next(self._request(), None) is None
        assert safe_next(self._request(), "") is None

    def test_header_injection_characters_are_rejected(self) -> None:
        """CR/LF/tab must never reach a Location header."""
        assert safe_next(self._request(), "/trips/\r\nX-Evil: 1") is None
        assert safe_next(self._request(), "/trips/\tx") is None

    def test_scheme_downgrade_is_rejected_on_a_secure_request(self) -> None:
        """Same host is not enough — an HTTPS page may not send you to HTTP."""
        request = self._request(secure=True)
        host = request.get_host()
        assert safe_next(request, f"http://{host}/trips/") is None
        assert safe_next(request, f"https://{host}/trips/") == f"https://{host}/trips/"


# ---------------------------------------------------------------------------
# sign_in_view — the form carries ``next`` to the server
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignInPageCarriesNext:
    """GET /sign-in/?next=… puts the destination in the form."""

    URL = reverse("accounts:sign_in")

    def test_safe_next_renders_as_a_hidden_field_and_data_attribute(
        self, client: Client
    ) -> None:
        response = client.get(self.URL, {"next": _SAFE_NEXT})
        body = response.content.decode()
        assert f'<input type="hidden" name="next" value="{_SAFE_NEXT}">' in body
        assert f'data-next="{_SAFE_NEXT}"' in body

    def test_unsafe_next_reaches_neither_the_form_nor_passkey_js(
        self, client: Client
    ) -> None:
        """Nothing to submit, nothing for passkey.js to read.

        Asserted on the two sinks rather than on the whole body: the page's
        canonical/og URLs echo the request's query string as they do on
        every page, and that reflection is escaped, inert, and none of this
        ticket's business.
        """
        response = client.get(self.URL, {"next": "https://evil.example/x"})
        body = response.content.decode()
        assert 'name="next"' not in body
        assert "data-next" not in body

    def test_an_already_signed_in_visitor_is_sent_to_next(self, client: Client) -> None:
        """Signing in was already done — honour the destination anyway."""
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(self.URL, {"next": _SAFE_NEXT})
        assert response.status_code == 302
        assert response["Location"] == _SAFE_NEXT

    def test_an_already_signed_in_visitor_falls_back_to_home(
        self, client: Client
    ) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(self.URL, {"next": "//evil.example"})
        assert response["Location"] == _HOME


# ---------------------------------------------------------------------------
# Password sign-in
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasswordSignInHonoursNext:
    """POST /sign-in/ with a password redirects to ``next`` when it is safe."""

    URL = reverse("accounts:sign_in")

    def _account_with_password(self) -> Any:
        """Return a verified account whose user has a usable password."""
        account = AccountFactory.create()
        account.user.set_password(_STRONG)
        account.user.save(update_fields=["password"])
        return account

    def test_safe_next_is_honoured(self, client: Client) -> None:
        account = self._account_with_password()
        response = client.post(
            self.URL,
            {"email": account.user.email, "password": _STRONG, "next": _SAFE_NEXT},
        )
        assert response.status_code == 302
        assert response["Location"] == _SAFE_NEXT

    def test_absent_next_falls_back_to_home(self, client: Client) -> None:
        account = self._account_with_password()
        response = client.post(
            self.URL, {"email": account.user.email, "password": _STRONG}
        )
        assert response["Location"] == reverse("public:home")

    def test_unsafe_next_falls_back_to_home(self, client: Client) -> None:
        account = self._account_with_password()
        response = client.post(
            self.URL,
            {
                "email": account.user.email,
                "password": _STRONG,
                "next": "https://evil.example/x",
            },
        )
        assert response["Location"] == reverse("public:home")

    def test_a_wrong_password_keeps_next_on_the_retry(self, client: Client) -> None:
        """A typo must not cost the destination."""
        account = self._account_with_password()
        response = client.post(
            self.URL,
            {"email": account.user.email, "password": "wrong", "next": _SAFE_NEXT},
        )
        assert response.status_code == 200
        assert f'value="{_SAFE_NEXT}"' in response.content.decode()


# ---------------------------------------------------------------------------
# Magic link — the emailed URL, and the confirm page behind it
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMagicLinkCarriesNext:
    """The account-access email's URL carries the destination."""

    URL = reverse("accounts:sign_in")

    def test_email_url_carries_the_encoded_next(self, client: Client) -> None:
        client.post(self.URL, {"email": "reader@example.com", "next": _SAFE_NEXT})
        assert len(mail.outbox) == 1
        assert "?next=%2Ftrips%2Fs%2Fabcdef123456%2F" in mail.outbox[0].body

    def test_unsafe_next_never_reaches_the_email(self, client: Client) -> None:
        client.post(
            self.URL,
            {"email": "reader@example.com", "next": "https://evil.example/x"},
        )
        assert len(mail.outbox) == 1
        assert "next=" not in mail.outbox[0].body
        assert "evil.example" not in mail.outbox[0].body


@pytest.mark.django_db
class TestAccountViewHonoursNext:
    """GET/POST /account/access/<token>/ carries ``next`` across the confirm."""

    def _url(self, email: str) -> str:
        """Return the account-access URL for a freshly minted token."""
        token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
        return reverse("accounts:account", kwargs={"token": token})

    def test_get_renders_next_as_a_hidden_field(self, client: Client) -> None:
        account = AccountFactory.create()
        response = client.get(self._url(account.user.email), {"next": _SAFE_NEXT})
        assert response.status_code == 200
        body = response.content.decode()
        assert f'<input type="hidden" name="next" value="{_SAFE_NEXT}">' in body

    def test_get_drops_an_unsafe_next(self, client: Client) -> None:
        """The confirm form carries no field, so the POST has nothing to read."""
        account = AccountFactory.create()
        response = client.get(self._url(account.user.email), {"next": "//evil.example"})
        assert 'name="next"' not in response.content.decode()

    def test_post_redirects_to_a_safe_next(self, client: Client) -> None:
        account = AccountFactory.create()
        response = client.post(self._url(account.user.email), {"next": _SAFE_NEXT})
        assert response.status_code == 302
        assert response["Location"] == _SAFE_NEXT
        assert client.session.get("_auth_user_id") == str(account.user.pk)

    def test_post_without_next_lands_on_the_map(self, client: Client) -> None:
        account = AccountFactory.create()
        response = client.post(self._url(account.user.email))
        assert response["Location"] == _VERIFIED_LANDING_URL

    def test_post_with_an_unsafe_next_lands_on_the_map(self, client: Client) -> None:
        """Tampering with the query redirects the tamperer nowhere new."""
        account = AccountFactory.create()
        response = client.post(
            self._url(account.user.email), {"next": "https://evil.example/x"}
        )
        assert response["Location"] == _VERIFIED_LANDING_URL


# ---------------------------------------------------------------------------
# Passkey
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyAuthResponseEchoesNext:
    """The auth response tells the browser where to go."""

    URL = reverse("accounts:passkey_auth_response")

    def _post(self, client: Client, body: dict[str, Any]) -> Any:
        """POST a credential body with verification stubbed to succeed."""
        user = UserFactory.create()
        _set_auth_challenge(client, "dGVzdA")
        with patch(
            "apps.accounts.views_passkey._verify_auth_response",
            return_value=user,
        ):
            return client.post(
                self.URL, data=json.dumps(body), content_type="application/json"
            )

    def test_safe_next_is_echoed_back(self, client: Client) -> None:
        response = self._post(client, {"id": "dGVzdA", "next": _SAFE_NEXT})
        assert response.status_code == 200
        assert json.loads(response.content)["next"] == _SAFE_NEXT

    def test_absent_next_answers_null(self, client: Client) -> None:
        """The browser then uses its own /account/ fallback."""
        response = self._post(client, {"id": "dGVzdA"})
        assert json.loads(response.content) == {"ok": True, "next": None}

    def test_unsafe_next_answers_null(self, client: Client) -> None:
        response = self._post(client, {"id": "dGVzdA", "next": "//evil.example"})
        assert json.loads(response.content)["next"] is None

    def test_a_non_string_next_is_ignored(self, client: Client) -> None:
        """The body is attacker-shaped as well as attacker-supplied."""
        response = self._post(client, {"id": "dGVzdA", "next": {"a": 1}})
        assert response.status_code == 200
        assert json.loads(response.content)["next"] is None


@pytest.mark.django_db
class TestSignInRefusesToBounceOffItself:
    """A ``next`` pointing back at the sign-in page is dropped."""

    URL = reverse("accounts:sign_in")

    def test_an_authenticated_visitor_lands_home_not_back_here(
        self, client: Client
    ) -> None:
        """The bounce the already-authenticated branch would otherwise make.

        Without the guard this answers 302 to the sign-in page, which — now
        carrying no ``next`` — answers 302 to the homepage: two redirects to
        reach where a missing ``next`` reaches in none. It terminates either
        way, so this asserts the hop count, not a hang.
        """
        user = UserFactory.create()
        client.force_login(user)

        response = client.get(self.URL, {"next": self.URL})

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("public:home")

    def test_the_hidden_field_is_empty_for_an_anonymous_visitor(
        self, client: Client
    ) -> None:
        """Nothing to post back, so the form cannot re-arm the bounce."""
        body = client.get(self.URL, {"next": self.URL}).content.decode()

        assert f'name="next" value="{self.URL}"' not in body

    def test_a_query_string_on_the_self_reference_is_dropped_too(
        self, client: Client
    ) -> None:
        """The path is what decides, not the whole string.

        ``/account/sign-in/?next=/x`` is still the sign-in page, and
        comparing full strings rather than paths would let it through.
        """
        user = UserFactory.create()
        client.force_login(user)

        response = client.get(self.URL, {"next": f"{self.URL}?next=/trips/"})

        assert response.headers["Location"] == reverse("public:home")
