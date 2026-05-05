"""
tests/bulletins/test_admin.py — Tests for BulletinAdmin.

Verifies that the backfill button triggers a pipeline run, shows
appropriate success/error messages, and rejects non-POST requests.
Also covers the SNOW-22 XSS-escaping helpers on the detail view, and
(SNOW-113) the empty-list / edge-case branches of the BulletinAdmin
display methods that XSS tests skip.
"""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.admin import site
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from bulletins.admin import BulletinAdmin
from bulletins.models import Bulletin, PipelineRun
from tests.factories import BulletinFactory, PipelineRunFactory


@pytest.fixture()
def admin_client() -> Client:
    """Return a Django test client logged in as a superuser."""
    user = User.objects.create_superuser("admin", "admin@test.com", "password")
    client = Client()
    client.force_login(user)
    return client


BACKFILL_URL = reverse("admin:bulletins_bulletin_backfill")


@pytest.mark.django_db
class TestBackfillView:
    """Tests for BulletinAdmin.backfill_view."""

    def test_post_triggers_pipeline_and_redirects(self, admin_client: Client) -> None:
        """A POST to the backfill URL runs the pipeline and redirects."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.SUCCESS,
            records_created=10,
            records_updated=2,
        )
        with patch("bulletins.admin.run_pipeline", return_value=run) as mock_run:
            response = admin_client.post(BACKFILL_URL)

        mock_run.assert_called_once_with(
            start=BulletinAdmin.BACKFILL_START,
            end=date.today(),
            triggered_by="admin backfill",
            dry_run=False,
            force=False,
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("admin:bulletins_bulletin_changelist")

    def test_success_message_shown(self, admin_client: Client) -> None:
        """A successful backfill shows a success message."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.SUCCESS,
            records_created=5,
            records_updated=1,
        )
        with patch("bulletins.admin.run_pipeline", return_value=run):
            response = admin_client.post(BACKFILL_URL, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "5 created" in str(messages[0])
        assert "1 updated" in str(messages[0])

    def test_failed_run_shows_error(self, admin_client: Client) -> None:
        """A failed pipeline run shows an error message."""
        run = PipelineRunFactory.create(
            status=PipelineRun.Status.FAILED,
            error_message="API timeout",
        )
        with patch("bulletins.admin.run_pipeline", return_value=run):
            response = admin_client.post(BACKFILL_URL, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "failed" in str(messages[0]).lower()

    def test_exception_shows_error(self, admin_client: Client) -> None:
        """An exception during the pipeline shows an error message."""
        with patch("bulletins.admin.run_pipeline", side_effect=RuntimeError("boom")):
            response = admin_client.post(BACKFILL_URL, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert "failed" in str(messages[0]).lower()

    def test_get_request_rejected(self, admin_client: Client) -> None:
        """A GET request to the backfill URL is rejected."""
        response = admin_client.get(BACKFILL_URL)
        assert response.status_code == 302

    def test_unauthenticated_user_redirected(self) -> None:
        """An unauthenticated user is redirected to the login page."""
        client = Client()
        response = client.post(BACKFILL_URL)
        assert response.status_code == 302
        assert "/login/" in response["Location"]


# ── XSS injection prevention (SNOW-22) ───────────────────────────────────────

_SCRIPT_TAG = "<script>alert('xss_snow22')</script>"


@pytest.mark.django_db
class TestAdminXssEscape:
    """
    Regression tests for SNOW-22: BulletinAdmin HTML table methods must
    escape SLF-provided fields rather than passing them raw through mark_safe.
    """

    def _admin(self) -> BulletinAdmin:
        """Return a BulletinAdmin instance bound to the default admin site."""
        from django.contrib.admin import site

        from bulletins.models import Bulletin as BulletinModel

        return BulletinAdmin(BulletinModel, site)

    def _bulletin(self, raw_properties: dict):
        """Create a Bulletin with the given raw_data properties payload."""
        from tests.factories import BulletinFactory

        return BulletinFactory.create(raw_data={"properties": raw_properties})

    def test_danger_ratings_escapes_main_value(self) -> None:
        """danger_ratings escapes a <script> injected into mainValue."""
        bulletin = self._bulletin(
            {
                "dangerRatings": [
                    {"mainValue": _SCRIPT_TAG, "validTimePeriod": "all_day"}
                ]
            }
        )
        html = self._admin().danger_ratings(bulletin)
        assert _SCRIPT_TAG not in html
        assert "&lt;script&gt;" in html or "alert" not in html

    def test_avalanche_problems_escapes_comment(self) -> None:
        """avalanche_problems escapes a <script> injected into problem comment."""
        bulletin = self._bulletin(
            {
                "dangerRatings": [],
                "avalancheProblems": [
                    {
                        "problemType": "new_snow",
                        "dangerRatingValue": "low",
                        "validTimePeriod": "all_day",
                        "comment": _SCRIPT_TAG,
                        "aspects": [],
                    }
                ],
            }
        )
        html = self._admin().avalanche_problems(bulletin)
        assert _SCRIPT_TAG not in html

    def test_aggregation_escapes_title(self) -> None:
        """aggregation escapes a <script> injected into the aggregation title."""
        bulletin = self._bulletin(
            {
                "dangerRatings": [],
                "customData": {
                    "CH": {
                        "aggregation": [
                            {
                                "category": "dry",
                                "validTimePeriod": "all_day",
                                "title": _SCRIPT_TAG,
                                "problemTypes": ["new_snow"],
                            }
                        ]
                    }
                },
            }
        )
        html = self._admin().aggregation(bulletin)
        assert _SCRIPT_TAG not in html


# ── SNOW-113: BulletinAdmin helper / display-method coverage ─────────────────


def _admin() -> BulletinAdmin:
    """Return a BulletinAdmin instance bound to the default admin site."""
    return BulletinAdmin(Bulletin, site)


class TestBulletinAdminHelpers:
    """
    Pure-function coverage for the static and instance helpers on
    BulletinAdmin that don't need a database.
    """

    def test_format_elevation_lower_and_upper(self) -> None:
        """Both bounds present → ``"{lower}m – {upper}m"`` range string."""
        result = BulletinAdmin._format_elevation(
            {"lowerBound": "1800", "upperBound": "2400"}
        )
        assert result == "1800m – 2400m"

    def test_format_elevation_lower_only(self) -> None:
        """Only ``lowerBound`` present → ``"Above {lower}m"``."""
        result = BulletinAdmin._format_elevation({"lowerBound": "2000"})
        assert result == "Above 2000m"

    def test_format_elevation_upper_only(self) -> None:
        """Only ``upperBound`` present → ``"Below {upper}m"``."""
        result = BulletinAdmin._format_elevation({"upperBound": "2400"})
        assert result == "Below 2400m"

    def test_format_elevation_empty_dict_returns_all_elevations(self) -> None:
        """An elevation dict with neither bound falls through to the default."""
        # The leading guard catches a falsy dict; passing a dict whose values
        # are all falsy reaches the trailing `return "All elevations"` branch.
        result = BulletinAdmin._format_elevation(
            {"lowerBound": None, "upperBound": None}
        )
        assert result == "All elevations"

    def test_format_elevation_none_returns_all_elevations(self) -> None:
        """``None`` input takes the leading guard to ``"All elevations"``."""
        assert BulletinAdmin._format_elevation(None) == "All elevations"

    def test_render_comment_none_returns_dash(self) -> None:
        """Falsy input returns the en-dash placeholder."""
        assert _admin()._render_comment(None) == "—"
        assert _admin()._render_comment("") == "—"

    def test_render_comment_empty_markdown_returns_dash(self) -> None:
        """When ``html_to_markdown`` returns "", the dash placeholder is used."""
        with patch("bulletins.admin.html_to_markdown", return_value=""):
            result = _admin()._render_comment("<div></div>")
        assert result == "—"

    def test_render_comment_with_content_returns_pre_block(self) -> None:
        """A non-empty markdown body is wrapped in a ``<pre>`` element."""
        result = _admin()._render_comment("<p>hello world</p>")
        assert "<pre" in result
        assert "hello world" in result


@pytest.mark.django_db
class TestBulletinAdminDisplayMethods:
    """
    Coverage for the ``@admin.display`` callables on BulletinAdmin —
    specifically the empty-list, missing-key, and content-rendering
    branches that the XSS tests do not exercise.
    """

    def _bulletin(self, raw_properties: dict) -> Bulletin:
        """Create a Bulletin with the given raw_data properties payload."""
        return BulletinFactory.create(raw_data={"properties": raw_properties})

    def test_danger_ratings_empty_returns_dash(self) -> None:
        """Empty ``dangerRatings`` array short-circuits to the dash."""
        bulletin = self._bulletin({"dangerRatings": []})
        assert _admin().danger_ratings(bulletin) == "—"

    def test_avalanche_problems_empty_returns_dash(self) -> None:
        """Empty ``avalancheProblems`` array short-circuits to the dash."""
        bulletin = self._bulletin({"avalancheProblems": []})
        assert _admin().avalanche_problems(bulletin) == "—"

    def test_aggregation_empty_returns_dash(self) -> None:
        """Empty ``customData.CH.aggregation`` array short-circuits to the dash."""
        bulletin = self._bulletin({"customData": {"CH": {"aggregation": []}}})
        assert _admin().aggregation(bulletin) == "—"

    def test_weather_forecast_with_comment(self) -> None:
        """A populated ``weatherForecast.comment`` renders inside ``<pre>``."""
        bulletin = self._bulletin(
            {"weatherForecast": {"comment": "<p>Snow showers</p>"}}
        )
        html = _admin().weather_forecast(bulletin)
        assert "<pre" in html
        assert "Snow showers" in html

    def test_weather_forecast_missing_key_returns_dash(self) -> None:
        """No ``weatherForecast`` key falls through to the dash placeholder."""
        bulletin = self._bulletin({})
        assert _admin().weather_forecast(bulletin) == "—"

    def test_weather_review_with_comment(self) -> None:
        """A populated ``weatherReview.comment`` renders inside ``<pre>``."""
        bulletin = self._bulletin(
            {"weatherReview": {"comment": "<p>Overnight clear</p>"}}
        )
        html = _admin().weather_review(bulletin)
        assert "<pre" in html
        assert "Overnight clear" in html

    def test_snowpack_structure_with_comment(self) -> None:
        """A populated ``snowpackStructure.comment`` renders inside ``<pre>``."""
        bulletin = self._bulletin(
            {"snowpackStructure": {"comment": "<p>Weak layer at depth</p>"}}
        )
        html = _admin().snowpack_structure(bulletin)
        assert "<pre" in html
        assert "Weak layer at depth" in html

    def test_tendency_empty_returns_dash(self) -> None:
        """An empty ``tendency`` list short-circuits to the dash."""
        bulletin = self._bulletin({"tendency": []})
        assert _admin().tendency(bulletin) == "—"

    def test_tendency_with_comments_renders_joined(self) -> None:
        """Multiple tendency entries with comments are joined into the body."""
        bulletin = self._bulletin(
            {
                "tendency": [
                    {"comment": "<p>Slight decrease tomorrow</p>"},
                    {"comment": "<p>Stable Saturday</p>"},
                ]
            }
        )
        html = _admin().tendency(bulletin)
        assert "<pre" in html
        assert "Slight decrease tomorrow" in html
        assert "Stable Saturday" in html

    def test_tendency_filters_non_dict_and_missing_comment(self) -> None:
        """Non-dict entries and dict entries without a ``comment`` are filtered out."""
        bulletin = self._bulletin(
            {
                "tendency": [
                    "not a dict",
                    {"validTime": "x"},
                    {"comment": ""},
                    {"comment": "<p>Real comment</p>"},
                ]
            }
        )
        html = _admin().tendency(bulletin)
        assert "Real comment" in html
        assert "not a dict" not in html

    def test_tendency_all_entries_filtered_returns_dash(self) -> None:
        """When all entries are filtered out, the dash placeholder is used."""
        bulletin = self._bulletin({"tendency": [{"validTime": "x"}, "string entry"]})
        assert _admin().tendency(bulletin) == "—"

    def test_raw_data_pretty_renders_json(self) -> None:
        """``raw_data_pretty`` wraps indented JSON in a ``<pre>`` element."""
        bulletin = self._bulletin({"foo": "bar"})
        html = _admin().raw_data_pretty(bulletin)
        assert "<pre" in html
        assert "&quot;foo&quot;" in html
        assert "&quot;bar&quot;" in html

    def test_render_model_pretty_renders_json(self) -> None:
        """``render_model_pretty`` wraps indented JSON in a ``<pre>`` element."""
        bulletin = BulletinFactory.create(render_model={"version": 4})
        html = _admin().render_model_pretty(bulletin)
        assert "<pre" in html
        assert "&quot;version&quot;" in html
        assert "4" in html
