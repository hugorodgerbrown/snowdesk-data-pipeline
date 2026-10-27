"""
tests/ingest/test_public.py — Tests for apps.ingest.public, the facade.

The facade's contract is its ``__all__``: every listed name must resolve, and
nothing it re-exports may be private. The boundary itself — that callers use
this module and nothing else — is tests/ingest/test_boundary.py.
"""

from apps.ingest import public


def test_every_listed_name_resolves() -> None:
    """``__all__`` names nothing the module does not define."""
    missing = [name for name in public.__all__ if not hasattr(public, name)]
    assert missing == []


def test_no_private_name_is_exported() -> None:
    """A leading underscore never crosses the boundary."""
    assert [name for name in public.__all__ if name.startswith("_")] == []
