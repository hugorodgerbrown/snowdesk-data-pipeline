"""
semgrep/rules.py — Self-test annotations for Snowdesk semgrep rules.

Each annotation is consumed by ``semgrep --test`` to verify that the rule
``factoryboy-bare-factory-call`` fires exactly where expected and is silent
everywhere else.

Stub classes/functions are defined inline so this file has no dependency on
the test suite and is valid Python that passes ruff format + lint.
"""


# ---------------------------------------------------------------------------
# Minimal stubs so the code below is syntactically and semantically valid.
# ---------------------------------------------------------------------------


class MicroRegion:
    """Stub model for test annotations."""


class BulletinFactory:
    """Stub factory — name ends in Factory to exercise the rule."""

    @classmethod
    def create(cls, **kwargs: object) -> "BulletinFactory":
        """Create and persist a stub instance."""
        return cls()

    @classmethod
    def build(cls, **kwargs: object) -> "BulletinFactory":
        """Build an unsaved stub instance."""
        return cls()

    @classmethod
    def create_batch(cls, size: int, **kwargs: object) -> list["BulletinFactory"]:
        """Create a list of stub instances."""
        return [cls() for _ in range(size)]


class MicroRegionFactory:
    """Stub factory for MicroRegion."""

    @classmethod
    def create(cls, **kwargs: object) -> MicroRegion:
        """Create and persist a stub MicroRegion."""
        return MicroRegion()


# ---------------------------------------------------------------------------
# Stubs for the exclusion cases.
# ---------------------------------------------------------------------------


class SubFactory:
    """FactoryBoy SubFactory — must not be flagged (used inside class bodies)."""

    def __init__(self, factory: type, **kwargs: object) -> None:
        """Initialise the SubFactory reference."""
        self.factory = factory


class RequestFactory:
    """Django test RequestFactory — must not be flagged."""

    def get(self, path: str) -> object:
        """Build a GET request."""
        return object()


class AsyncRequestFactory:
    """Django async test factory — must not be flagged."""

    def get(self, path: str) -> object:
        """Build a GET request."""
        return object()


class factory:
    """Stub mimicking the ``factory`` module namespace for exclusion cases."""

    @staticmethod
    def LazyAttribute(fn: object) -> object:
        """Stub for factory.LazyAttribute."""
        return fn

    @staticmethod
    def LazyFunction(fn: object) -> object:
        """Stub for factory.LazyFunction."""
        return fn


# ---------------------------------------------------------------------------
# Test cases: bare call — rule SHOULD fire.
# ---------------------------------------------------------------------------

# ruleid: factoryboy-bare-factory-call
_bad_bulletin = BulletinFactory()

# ruleid: factoryboy-bare-factory-call
_bad_region = MicroRegionFactory()

# ---------------------------------------------------------------------------
# Test cases: explicit classmethods — rule MUST NOT fire.
# ---------------------------------------------------------------------------

# ok: factoryboy-bare-factory-call
_ok_create = BulletinFactory.create()

# ok: factoryboy-bare-factory-call
_ok_build = BulletinFactory.build()

# ok: factoryboy-bare-factory-call
_ok_batch = BulletinFactory.create_batch(3)

# ok: factoryboy-bare-factory-call
_ok_create_region = MicroRegionFactory.create()

# ---------------------------------------------------------------------------
# Test cases: excluded names — rule MUST NOT fire.
# ---------------------------------------------------------------------------

# ok: factoryboy-bare-factory-call
_sub = SubFactory(MicroRegionFactory)

# ok: factoryboy-bare-factory-call
_rf = RequestFactory()

# ok: factoryboy-bare-factory-call
_arf = AsyncRequestFactory()

# ok: factoryboy-bare-factory-call
_lazy_attr = factory.LazyAttribute(lambda obj: obj)

# ok: factoryboy-bare-factory-call
_lazy_func = factory.LazyFunction(list)
