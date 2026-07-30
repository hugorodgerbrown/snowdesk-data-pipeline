"""
apps/analytics/exceptions.py — Custom exception types for the analytics app.

Defines ``AnalyticsPIIError``, raised by ``analytics.track()`` when the
caller passes event properties that contain a blocked PII key (``email``,
``ip``, ``token``, or ``credential_id``).  The exception fires before any
network call so no PII can leak to PostHog even in degraded conditions.
"""


class AnalyticsPIIError(ValueError):
    """Raised when event properties contain a personally-identifiable key.

    Inherits from ``ValueError`` so callers that catch broad exceptions in
    tests can assert the specific type without importing this class.
    """
