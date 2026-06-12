"""
subscriptions/logging_utils.py — Safe logging helpers for the subscriptions app.

Provides utilities for producing log-safe representations of sensitive values so
that plaintext email addresses never appear in log output (and therefore never in
log files, which are not encrypted at rest).

Public API
----------
``mask_email(email: str) -> str``
    Return a masked form of an email address suitable for log messages.
"""

from __future__ import annotations


def mask_email(email: str) -> str:
    """
    Return a masked representation of an email address for use in log messages.

    Preserves just enough structure for log triage without exposing the full
    address.  The masking rules are:

    - If ``email`` contains no ``@`` (or is empty), return ``"***"``.
    - Otherwise return ``f"{local[:1]}***@{domain}"`` where ``local`` is the
      part before the first ``@`` and ``domain`` is everything after it.  An
      empty local part (e.g. ``"@example.com"``) still produces ``"***@example.com"``.

    Args:
        email: The email address string to mask.

    Returns:
        A masked string safe for inclusion in log output.

    Examples::

        >>> mask_email("alice@example.com")
        'a***@example.com'
        >>> mask_email("a@example.com")
        'a***@example.com'
        >>> mask_email("@example.com")
        '***@example.com'
        >>> mask_email("noop")
        '***'
        >>> mask_email("")
        '***'

    """
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"
