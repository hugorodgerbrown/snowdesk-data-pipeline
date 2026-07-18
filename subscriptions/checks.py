"""
subscriptions/checks.py — Django system checks for the subscriptions app.

Validates that ``push_config.VAPID_CLAIM_EMAIL`` — the JWT ``sub`` claim
sent with every Web Push dispatch — is a well-formed ``mailto:`` or
``https:`` URI, per the VAPID spec (RFC 8292 §2). Push services (notably
Apple's APNs, which backs Safari/iOS Web Push) reject a JWT whose ``sub``
claim isn't one of those two schemes with a 403, and the failure only
shows up at push-dispatch time in production — this check fails fast at
``manage.py check`` time instead (SNOW-380).
"""

from __future__ import annotations

from typing import Any

from django.core.checks import Error, Tags, register

# E001 — VAPID_CLAIM_EMAIL missing a mailto: or https: scheme.
CHECK_ID_PREFIX = "subscriptions.push_config"


@register(Tags.compatibility)
def check_vapid_claim_email(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Verify ``push_config.VAPID_CLAIM_EMAIL`` starts with ``mailto:`` or ``https:``.

    RFC 8292 requires the VAPID JWT's ``sub`` claim to be a ``mailto:`` or
    ``https:`` URI identifying a contact for the application server. Push
    services use it to reach out about delivery problems; a malformed
    value causes every push dispatch to be rejected with 403.
    """
    from subscriptions.push_config import VAPID_CLAIM_EMAIL

    if VAPID_CLAIM_EMAIL.startswith(("mailto:", "https:")):
        return []
    return [
        Error(
            f"VAPID_CLAIM_EMAIL is not a valid VAPID subject: {VAPID_CLAIM_EMAIL!r}",
            hint=(
                "Set VAPID_CLAIM_EMAIL to a 'mailto:' or 'https:' URI (RFC 8292 "
                "§2), e.g. 'mailto:ops@yourdomain.com'. An invalid subject "
                "causes push services (notably Apple's APNs) to reject every "
                "push dispatch with a 403."
            ),
            id=f"{CHECK_ID_PREFIX}.E001",
        )
    ]
