"""
tests/routes/test_migrations.py — Tests for apps.routes data migrations.

routes.0002_route_descent_m._backfill_descent:
  a legacy row with elevation in its stored points gets a descent;
  a legacy row whose points carry no elevation keeps its null;
  the figure is a positive magnitude, and drops are summed rather than
    netted against climbs;
  a gap in the stored series is skipped, not read as a plunge;
  a row that already has a descent is rewritten from its own geometry,
    so a re-run is idempotent.

The migration's own function is called directly with a stand-in ``apps``
registry rather than run through Django's migration executor: the
behaviour under test is the arithmetic and the null rule, and the
executor would add a schema rebuild per test for nothing. ``schema_editor``
is unused by the function and passed as None.

Why the figure is measured on the simplified track at all — and why that
is the right trade for rows created before the field existed — is argued
in the migration's own module docstring.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

from apps.routes.models import Route
from tests.factories import RouteFactory, UserFactory

# ``import_module`` rather than a plain import: the module name starts with
# a digit, so it is not a legal Python identifier and cannot be written as
# ``from apps.routes.migrations.0002_… import …``.
_MIGRATION = import_module("apps.routes.migrations.0002_route_descent_m")


class _StubApps:
    """Minimal stand-in for the historical app registry.

    ``_backfill_descent`` only ever calls ``get_model("routes", "Route")``,
    and the concrete model is a faithful stand-in here because the
    migration is the latest one — no field it touches has moved since.
    """

    def get_model(self, app_label: str, model_name: str) -> Any:
        """Return the concrete model for the requested label.

        Args:
            app_label: The app label being asked for.
            model_name: The model name being asked for.

        Returns:
            The ``Route`` model.

        """
        assert (app_label, model_name) == ("routes", "Route")
        return Route


def _run_backfill() -> None:
    """Invoke the migration's backfill against the test database."""
    _MIGRATION._backfill_descent(_StubApps(), None)


@pytest.mark.django_db
class TestBackfillDescent:
    """The one-off backfill of Route.descent_m onto pre-existing rows."""

    def test_a_row_with_elevation_gets_its_descent(self) -> None:
        """1500 → 1400 → 1600 → 1550 drops 100 then 50."""
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            descent_m=None,
            points=[
                [7.4, 46.1, 1500.0],
                [7.41, 46.11, 1400.0],
                [7.42, 46.12, 1600.0],
                [7.43, 46.13, 1550.0],
            ],
        )

        _run_backfill()

        route.refresh_from_db()
        assert route.descent_m == pytest.approx(150.0)

    def test_the_figure_is_a_positive_magnitude(self) -> None:
        """Never signed — Route.descent_m is a magnitude everywhere."""
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            descent_m=None,
            points=[[7.4, 46.1, 2000.0], [7.41, 46.11, 1000.0]],
        )

        _run_backfill()

        route.refresh_from_db()
        assert route.descent_m == pytest.approx(1000.0)

    def test_climbs_are_not_netted_against_drops(self) -> None:
        """A lap that climbs 500 and drops 500 has 500 of descent, not zero."""
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            descent_m=None,
            points=[
                [7.4, 46.1, 1000.0],
                [7.41, 46.11, 1500.0],
                [7.42, 46.12, 1000.0],
            ],
        )

        _run_backfill()

        route.refresh_from_db()
        assert route.descent_m == pytest.approx(500.0)

    def test_a_row_without_elevation_keeps_its_null(self) -> None:
        """No elevation in the geometry means the answer stays "unknown".

        For such a row the null is not a gap waiting to be filled — it is
        the permanent, correct answer, because the source GPX never
        carried an ``<ele>`` in the first place.
        """
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            ascent_m=None,
            descent_m=None,
            points=[[7.4, 46.1, None], [7.41, 46.11, None]],
        )

        _run_backfill()

        route.refresh_from_db()
        assert route.descent_m is None

    def test_a_gap_in_the_series_is_skipped_not_read_as_a_plunge(self) -> None:
        """One missing reading must not become a 1500-metre drop to zero."""
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            descent_m=None,
            points=[
                [7.4, 46.1, 1500.0],
                [7.41, 46.11, None],
                [7.42, 46.12, 1400.0],
            ],
        )

        _run_backfill()

        route.refresh_from_db()
        # Neither pair has two readings, so nothing legal to measure — but
        # the series is not empty, so the row is still given its measured 0.
        assert route.descent_m == pytest.approx(0.0)

    def test_a_two_slot_position_is_treated_as_no_elevation(self) -> None:
        """A [lon, lat] pair has no third slot to read; it must not IndexError."""
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            ascent_m=None,
            descent_m=None,
            points=[[7.4, 46.1], [7.41, 46.11]],
        )

        _run_backfill()

        route.refresh_from_db()
        assert route.descent_m is None

    def test_rerunning_is_idempotent(self) -> None:
        """A second pass rewrites the same figure from the same geometry."""
        user = UserFactory.create()
        route = RouteFactory.create(
            user=user,
            descent_m=None,
            points=[[7.4, 46.1, 1800.0], [7.41, 46.11, 1500.0]],
        )

        _run_backfill()
        route.refresh_from_db()
        first = route.descent_m

        _run_backfill()
        route.refresh_from_db()

        assert route.descent_m == first == pytest.approx(300.0)
