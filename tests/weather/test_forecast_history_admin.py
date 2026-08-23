"""
tests/weather/test_forecast_history_admin.py — ForecastCellWeatherHistoryAdmin.

The admin is the only read surface on the convergence series (SNOW-575
ships no public UI or API), so these cover that it is registered, renders,
and orders a day's rows the way a convergence series is read — oldest
issue first.

Covers:
  - The model is registered with the admin site.
  - The changelist renders for a staff user and lists rows.
  - Changelist ordering is -valid_for_date then issued_date ascending.
  - The lead_days filter is available.
  - An anonymous user is redirected to the admin login page.
"""

import datetime

import pytest
from django.contrib import admin as django_admin
from django.test import Client
from django.urls import reverse

from apps.weather.models import ForecastCellWeatherHistory
from tests.factories import (
    ForecastCellFactory,
    ForecastCellWeatherHistoryFactory,
    UserFactory,
)

CHANGELIST_URL = reverse("admin:weather_forecastcellweatherhistory_changelist")
VALID_DAY = datetime.date(2026, 5, 8)


@pytest.fixture()
def staff_client() -> Client:
    """Return a Django test client logged in as a superuser."""
    user = UserFactory.create(is_superuser=True)
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestRegistration:
    """The model is wired into the admin site."""

    def test_model_is_registered(self) -> None:
        """ForecastCellWeatherHistory has an explicit admin class."""
        assert ForecastCellWeatherHistory in django_admin.site._registry

    def test_lead_days_is_filterable(self) -> None:
        """lead_days is offered as a filter — the axis analysis slices on."""
        model_admin = django_admin.site._registry[ForecastCellWeatherHistory]
        assert "lead_days" in model_admin.list_filter


@pytest.mark.django_db
class TestChangelist:
    """The changelist renders and orders rows for reading a series."""

    def test_renders_for_staff(self, staff_client: Client) -> None:
        """A staff GET returns 200 once rows exist."""
        ForecastCellWeatherHistoryFactory.create()
        response = staff_client.get(CHANGELIST_URL)
        assert response.status_code == 200

    def test_orders_oldest_issue_first_within_a_day(self, staff_client: Client) -> None:
        """Rows for one forecast day read from earliest issue to latest."""
        point = ForecastCellFactory.create()
        for lead in (0, 4, 2):
            ForecastCellWeatherHistoryFactory.create(
                forecast_cell=point,
                valid_for_date=VALID_DAY,
                issued_date=VALID_DAY - datetime.timedelta(days=lead),
                lead_days=lead,
            )

        response = staff_client.get(CHANGELIST_URL)

        assert response.status_code == 200
        result_leads = [row.lead_days for row in response.context["cl"].result_list]
        assert result_leads == [4, 2, 0]

    def test_anonymous_user_redirected_to_login(self) -> None:
        """An unauthenticated request is bounced to the admin login."""
        response = Client().get(CHANGELIST_URL)
        assert response.status_code == 302
        assert reverse("admin:login") in response["Location"]
