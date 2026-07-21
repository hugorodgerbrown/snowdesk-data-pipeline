"""tests/analytics/test_views.py — POST /api/telemetry receiver.

Covers the first-party telemetry pipeline server-side (SNOW-381, spec
§16). The endpoint validates the eight-field envelope, forwards each
event to ``analytics.track()`` (which is patched here to avoid a real
PostHog call), and returns 204 on success. Failure modes 400 / 405 /
413 / 415 are exercised so a broken client doesn't discover them in
production.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from analytics.schema import MAX_PAYLOAD_BYTES

# Every test in this module hits the Django test client, which triggers
# the IdempotencyMiddleware / PosthogContextMiddleware DB access even
# when the endpoint itself is read-only. Marking module-wide is cleaner
# than annotating every method.
pytestmark = pytest.mark.django_db

# All tests here override the PostHog key so the ``analytics.track()``
# wrapper actually reaches its inner ``posthog.capture`` call — which we
# patch out per-test. Without the key the wrapper short-circuits and the
# patched capture is never invoked, hiding regressions.
_POSTHOG = override_settings(
    POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
)


def _valid_event(
    name: str = "pwa.sw.installed", **overrides: object
) -> dict[str, object]:
    """Return a minimal envelope for the given event name."""
    envelope: dict[str, object] = {
        "event": name,
        "timestamp": "2026-07-16T10:00:00+00:00",
        "client_version": "2026.07.16.abc",
        "session_id": "sess-1",
        "user_id": "user-42",
        "platform": "web",
        "install_state": "installed",
        "sw_state": "activated",
        "connection": "wifi",
        "properties": {},
    }
    envelope.update(overrides)
    return envelope


class TestReceiverAcceptsValidPayload:
    """The receiver forwards well-formed envelopes to ``posthog.capture``."""

    @_POSTHOG
    def test_single_envelope_returns_204(self) -> None:
        """A single envelope (sendBeacon fast path) is accepted."""
        with patch("posthog.capture") as mock_capture:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/json",
            )
        assert response.status_code == 204
        assert mock_capture.call_count == 1

    @_POSTHOG
    def test_batched_envelope_forwards_each_event(self) -> None:
        """``{"events": [e1, e2, e3]}`` fires three captures."""
        batch = {
            "events": [
                _valid_event("pwa.sw.installed"),
                _valid_event("pwa.install.prompted"),
                _valid_event("pwa.freshness.stale"),
            ]
        }
        with patch("posthog.capture") as mock_capture:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(batch),
                content_type="application/json",
            )
        assert response.status_code == 204
        assert mock_capture.call_count == 3
        forwarded_events = [
            call.kwargs["event"] for call in mock_capture.call_args_list
        ]
        assert forwarded_events == [
            "pwa.sw.installed",
            "pwa.install.prompted",
            "pwa.freshness.stale",
        ]

    @_POSTHOG
    def test_json_subtype_content_type_accepted(self) -> None:
        """``application/vnd.api+json`` and similar +json subtypes pass through."""
        with patch("posthog.capture") as mock_capture:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/vnd.api+json",
            )
        assert response.status_code == 204
        assert mock_capture.call_count == 1

    @_POSTHOG
    def test_envelope_context_lifted_into_properties(self) -> None:
        """Envelope-level context (platform, sw_state, …) is queryable in PostHog."""
        with patch("posthog.capture") as mock_capture:
            Client().post(
                "/api/telemetry",
                data=json.dumps(
                    _valid_event(
                        "pwa.sw.installed",
                        platform="ios",
                        sw_state="activated",
                        connection="cellular",
                        properties={"registration_ms": 42},
                    )
                ),
                content_type="application/json",
            )
        props = mock_capture.call_args.kwargs["properties"]
        assert props["platform"] == "ios"
        assert props["sw_state"] == "activated"
        assert props["connection"] == "cellular"
        assert props["registration_ms"] == 42
        assert props["client_timestamp"] == "2026-07-16T10:00:00+00:00"

    @_POSTHOG
    def test_user_id_wins_over_session_id(self) -> None:
        """distinct_id resolution prefers user_id, then session_id, then _anon."""
        with patch("posthog.capture") as mock_capture:
            Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event(user_id="U", session_id="S")),
                content_type="application/json",
            )
        assert mock_capture.call_args.kwargs["distinct_id"] == "U"

    @_POSTHOG
    def test_session_id_used_when_user_id_absent(self) -> None:
        """distinct_id falls back to session_id when user_id is None."""
        with patch("posthog.capture") as mock_capture:
            Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event(user_id=None, session_id="S-only")),
                content_type="application/json",
            )
        assert mock_capture.call_args.kwargs["distinct_id"] == "S-only"

    @_POSTHOG
    def test_anon_sentinel_when_both_ids_stripped(self) -> None:
        """distinct_id falls back to ``_anon`` when both IDs are None (opt-out)."""
        with patch("posthog.capture") as mock_capture:
            Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event(user_id=None, session_id=None)),
                content_type="application/json",
            )
        assert mock_capture.call_args.kwargs["distinct_id"] == "_anon"

    @_POSTHOG
    def test_posthog_failure_is_swallowed(self) -> None:
        """A PostHog exception is caught by analytics.track — 204 still returned."""
        with patch("posthog.capture", side_effect=RuntimeError("PostHog is down")):
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/json",
            )
        assert response.status_code == 204


class TestReceiverRejectsMalformedInput:
    """The receiver responds with 4xx codes for malformed payloads."""

    def test_get_returns_405(self) -> None:
        """Only POST is accepted."""
        response = Client().get("/api/telemetry")
        assert response.status_code == 405

    def test_wrong_content_type_returns_415(self) -> None:
        """``application/x-www-form-urlencoded`` (or anything non-JSON) is rejected."""
        response = Client().post(
            "/api/telemetry",
            data="event=pwa.sw.installed",
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 415
        assert json.loads(response.content)["error"] == "unsupported_media_type"

    def test_oversized_payload_returns_413(self) -> None:
        """Payloads > MAX_PAYLOAD_BYTES are rejected without parsing."""
        # Build a valid envelope then pad the JSON body past the cap.
        payload = {"events": [_valid_event()]}
        payload["events"][0]["properties"] = {"pad": "x" * (MAX_PAYLOAD_BYTES + 10)}
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert response.status_code == 413
        assert json.loads(response.content)["error"] == "payload_too_large"

    def test_invalid_json_returns_400(self) -> None:
        """A body that isn't valid JSON returns 400 with ``invalid_json``."""
        response = Client().post(
            "/api/telemetry",
            data="{not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert json.loads(response.content)["error"] == "invalid_json"

    def test_missing_required_key_returns_400(self) -> None:
        """A payload missing ``event`` is rejected."""
        bad = _valid_event()
        del bad["event"]
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(bad),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert json.loads(response.content)["error"] == "invalid_envelope"

    def test_unknown_event_name_returns_400(self) -> None:
        """Event names outside the allowlist are rejected at the boundary."""
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(_valid_event(event="pwa.not_a_real_event")),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_unknown_top_level_key_returns_400(self) -> None:
        """A typo like ``even`` for ``event`` doesn't disappear into PostHog silently."""
        bad = _valid_event()
        bad["typo_key"] = "oops"
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(bad),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_bad_timestamp_returns_400(self) -> None:
        """Non-ISO-8601 timestamps are rejected."""
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(_valid_event(timestamp="yesterday-ish")),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_empty_events_list_returns_400(self) -> None:
        """``{"events": []}`` is rejected — clients must not POST empty batches."""
        response = Client().post(
            "/api/telemetry",
            data=json.dumps({"events": []}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @_POSTHOG
    def test_pii_property_returns_400(self) -> None:
        """A client-supplied PII key (email, ip, token, credential_id) is rejected."""
        bad = _valid_event(properties={"email": "x@example.com"})
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(bad),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert json.loads(response.content)["error"] == "pii_property"


class TestReceiverRateLimit:
    """The rate-limiter drops excess requests silently with 204."""

    @_POSTHOG
    def test_limited_request_drops_without_forwarding(self) -> None:
        """When ``request.limited`` is True the receiver drops with 204.

        Drives the unwrapped view function directly with a request that
        has ``limited`` pre-set — sidesteps ``django-ratelimit``'s
        internals (which the tests can't reach through decorator
        wrapping) while still exercising the branch in
        ``telemetry_receive``.
        """
        from django.test import RequestFactory

        from analytics.views import telemetry_receive

        request = RequestFactory().post(
            "/api/telemetry",
            data=json.dumps(_valid_event()),
            content_type="application/json",
        )
        request.limited = True  # type: ignore[attr-defined]

        with patch("posthog.capture") as mock_capture:
            # The @ratelimit decorator sets request.limited; when a caller
            # (or, here, the test) has already set it, the decorated view
            # sees the same True value on entry.
            response = telemetry_receive(request)

        assert response.status_code == 204
        mock_capture.assert_not_called()


class TestReceiverNoPostHogKey:
    """With no key configured the endpoint still 204s but sends nothing."""

    @override_settings(POSTHOG_API_KEY="")
    def test_no_op_when_key_empty(self) -> None:
        """No key → analytics.track short-circuits → posthog.capture not called."""
        with patch("posthog.capture") as mock_capture:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/json",
            )
        assert response.status_code == 204
        mock_capture.assert_not_called()

    @override_settings(POSTHOG_API_KEY="")
    def test_analytics_track_never_called_when_key_empty(self) -> None:
        """SNOW-384: the receiver's own gate short-circuits before the
        per-event forward loop — ``analytics.track`` is never called at
        all, not merely no-opping internally.
        """
        with patch("analytics.views.analytics.track") as mock_track:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps({"events": [_valid_event(), _valid_event()]}),
                content_type="application/json",
            )
        assert response.status_code == 204
        mock_track.assert_not_called()


class TestReceiverKeyGatePositive:
    """SNOW-384: with a key configured, the forward loop still runs."""

    @_POSTHOG
    def test_analytics_track_called_when_key_set(self) -> None:
        """SNOW-384: with a key configured, the forward loop still runs."""
        with patch("analytics.views.analytics.track") as mock_track:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/json",
            )
        assert response.status_code == 204
        mock_track.assert_called_once()


class TestReceiverMasterSwitch:
    """PWA_TELEMETRY_ENABLED=False accepts-and-drops before parsing."""

    @override_settings(POSTHOG_API_KEY="test-key", PWA_TELEMETRY_ENABLED=False)
    def test_disabled_returns_204_without_forwarding(self) -> None:
        """Master switch off → 204, ``analytics.track`` never called.

        The key is populated to prove the master switch wins over the
        key gate — with telemetry disabled, nothing forwards regardless.
        """
        with patch("analytics.views.analytics.track") as mock_track:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/json",
            )
        assert response.status_code == 204
        mock_track.assert_not_called()

    @override_settings(POSTHOG_API_KEY="test-key", PWA_TELEMETRY_ENABLED=False)
    def test_disabled_drops_before_validation(self) -> None:
        """The switch short-circuits ahead of schema validation.

        A payload that would otherwise 400 (unknown event name) still
        gets a 204 when telemetry is disabled — the disabled path never
        reaches ``_parse_events``.
        """
        response = Client().post(
            "/api/telemetry",
            data=json.dumps(_valid_event(name="not.an.allowed.event")),
            content_type="application/json",
        )
        assert response.status_code == 204


class TestReceiverCsrfExempt:
    """The endpoint is CSRF-exempt so sendBeacon can post without a token."""

    @_POSTHOG
    def test_no_csrf_token_still_accepted(self) -> None:
        """Enforced-CSRF client posts without a token — endpoint accepts anyway."""
        with patch("posthog.capture"):
            response = Client(enforce_csrf_checks=True).post(
                "/api/telemetry",
                data=json.dumps(_valid_event()),
                content_type="application/json",
            )
        assert response.status_code == 204
