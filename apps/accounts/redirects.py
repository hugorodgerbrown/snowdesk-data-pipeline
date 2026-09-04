"""
apps/accounts/redirects.py — validation for post-sign-in ``next`` destinations.

Every sign-in path (password, magic link, passkey) accepts a ``next``
destination so a recipient who opens a trip share link, signs in, and is
returned to the trip rather than dumped on the map (SNOW-825). That
parameter is attacker-supplied on every one of those paths, and a
destination the server does not check is an **open redirector**: a phishing
link of the form ``https://snowdesk.info/sign-in/?next=https://evil.example/``
wears Snowdesk's domain in the message, in the browser bar, and in the
sign-in page the victim actually authenticates against, then hands them to
somebody else's page. That is the whole reason threading ``next`` through
sign-in is not a two-line change, and why every read of it goes through
``safe_next``.

``safe_next`` is the single gate. It answers ``None`` for anything it
cannot vouch for — empty, missing, foreign-host, scheme-downgrading, or
header-injecting — so every caller can write ``safe_next(...) or
<its own default>`` and never has to decide what "unsafe" looks like.
"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

# Characters that must never reach a ``Location`` header. Django raises
# ``BadHeaderError`` on a header value carrying CR/LF, and ``urlsplit``
# silently strips tab and newline before Django's host check sees them — so a
# candidate holding one could pass validation and then either 500 or redirect
# somewhere the check never looked at. Reject them outright instead.
_FORBIDDEN_CHARS = frozenset("\r\n\t")


def safe_next(request: HttpRequest, candidate: str | None) -> str | None:
    r"""Return ``candidate`` when it is a safe same-site redirect target, else None.

    Safe means: non-empty, free of header-injection characters, and accepted
    by ``django.utils.http.url_has_allowed_host_and_scheme`` for this
    request's own host — which rejects an absolute URL to another host, a
    protocol-relative ``//evil.example``, and the backslash variants browsers
    normalise into one (``/\evil.example``, ``\\evil.example``). On an
    HTTPS request an ``http://`` destination is rejected too, so a
    same-host answer cannot be used to downgrade the scheme.

    Never raises: a caller can pass whatever arrived in the query string,
    the POST body or a JSON payload straight in.

    Args:
        request: The current request, whose host and scheme define "same site".
        candidate: The untrusted destination — typically ``request.GET.get
            ("next")`` — or ``None`` when the caller has nothing to offer.

    Returns:
        The destination to redirect to, or ``None`` when the caller should
        fall back to its own default.

    """
    if not candidate:
        return None

    if _FORBIDDEN_CHARS.intersection(candidate):
        return None

    # Django strips surrounding whitespace before validating, so strip here
    # too — otherwise the value validated and the value redirected to differ.
    destination = candidate.strip()

    if not url_has_allowed_host_and_scheme(
        url=destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return None

    return destination
