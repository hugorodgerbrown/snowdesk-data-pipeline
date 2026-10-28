"""
apps/ingest/public.py — The one module the rest of Snowdesk imports from ingest.

Everything outside ``apps/ingest`` that needs ingest's code imports it from
here, never from a module inside the package. That keeps the boundary in one
file: what is listed below is the contract a rehosted ingest service must keep,
and anything not listed may move or change without a caller noticing.
``tests/ingest/test_boundary.py`` fails the build on a reach past this module
from ``apps/`` or ``config/`` (SNOW-1248).

Tests and the offline scripts under ``scripts/`` are exempt: a test may
exercise a private helper, and the scripts run without Django, so they import
the pure module they need directly.

The names are grouped by what they are for:

  Bulletin envelopes and archives
      Outcome tags shared by the three pipelines, unwrapping a provider's
      response into a flat list, and the NDJSON stash files.
  SLF
      Response shape detection, timestamp parsing and the archive PDF URL.
  ALBINA
      The on-demand PDF URL.
  Météo-France
      The DPBRA translator, the bulletin identity grammar and the Alpine
      massif catalogue.
  Weather
      Parsing an Open-Meteo forecast response, and the two shapes it returns.
  A point
      What Open-Meteo says about one coordinate — ``fetch_forecast`` over a
      date range (``history_base_url`` for the backfill) and
      ``fetch_elevation`` — and its what3words address, ``convert_to_3wa``
      (``fake_address`` offline). Each takes a coordinate, never a
      ``Location`` (SNOW-1249).
  Fetching bulletins
      ``fetch_bulletins`` walks one provider and yields ``NormalisedBulletin``
      and ``FetchOutcome`` items; what to do with each is the caller's
      (SNOW-1249). Plus the one Météo-France archive lookup a backfill needs.
"""

from apps.ingest.normalise.albina import albina_pdf_url
from apps.ingest.normalise.fetcher_common import (
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UPDATED,
    merge_ndjson_records,
    normalise_bulletin_response,
    write_ndjson_archive,
)
from apps.ingest.normalise.meteofrance.identity import (
    BULLETIN_ID_RE,
    build_bulletin_id,
    compact_publication_stamp,
)
from apps.ingest.normalise.meteofrance.massifs import (
    ALPINE_MASSIFS,
    SLUG_TO_CODE,
    normalise_slug,
    slug_to_region_id,
)
from apps.ingest.normalise.meteofrance.translator import (
    MeteoFranceDelegatedRegionError,
    MeteoFranceTranslationError,
    format_comment_as_html,
    parse_dpbra_xml,
)
from apps.ingest.normalise.slf import (
    CAAML_SHAPE_AGGREGATED,
    CAAML_SHAPE_PER_REGION,
    CAAML_SHAPE_UNKNOWN,
    DEFAULT_LANG as SLF_DEFAULT_LANG,
    CaamlPayloadShape,
    detect_caaml_shape,
    normalise_slf_response,
    parse_dt,
    resolve_issued_at,
    slf_pdf_url,
)
from apps.ingest.normalise.slf_archive import (
    merge as merge_slf_archive,
    read_archive as read_slf_archive,
    write_archive as write_slf_archive,
)
from apps.ingest.normalise.weather import (
    DAILY_VARIABLES,
    HOURLY_VARIABLES,
    ForecastDay,
    HourlyRow,
    daily_dates,
    daily_fields,
    daily_max_freezing_level,
    day_is_complete,
    forecast_entry,
    hourly_rows_for_day,
    parse_dt_preserve_offset,
)
from apps.ingest.runs import (
    OUTCOME_DELEGATED,
    OUTCOME_DUPLICATE,
    OUTCOME_SKIPPED_NEWER,
    PROVIDER_ALBINA,
    PROVIDER_METEOFRANCE,
    PROVIDER_SLF,
    FetchOutcome,
    FetchStats,
    NormalisedBulletin,
    fetch_bulletins,
)
from apps.ingest.sources.meteofrance import meteofrance_pdf_url, pdf_session
from apps.ingest.sources.open_meteo import (
    fetch_elevation,
    fetch_forecast,
    history_base_url,
)
from apps.ingest.sources.slf import PAGE_SIZE as SLF_PAGE_SIZE
from apps.ingest.sources.what3words import convert_to_3wa, fake_address

__all__ = [
    "ALPINE_MASSIFS",
    "BULLETIN_ID_RE",
    "CAAML_SHAPE_AGGREGATED",
    "CAAML_SHAPE_PER_REGION",
    "CAAML_SHAPE_UNKNOWN",
    "DAILY_VARIABLES",
    "HOURLY_VARIABLES",
    "OUTCOME_CREATED",
    "OUTCOME_DELEGATED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "OUTCOME_SKIPPED_NEWER",
    "OUTCOME_UPDATED",
    "PROVIDER_ALBINA",
    "PROVIDER_METEOFRANCE",
    "PROVIDER_SLF",
    "SLF_DEFAULT_LANG",
    "SLF_PAGE_SIZE",
    "SLUG_TO_CODE",
    "CaamlPayloadShape",
    "FetchOutcome",
    "FetchStats",
    "ForecastDay",
    "HourlyRow",
    "MeteoFranceDelegatedRegionError",
    "MeteoFranceTranslationError",
    "NormalisedBulletin",
    "albina_pdf_url",
    "build_bulletin_id",
    "compact_publication_stamp",
    "convert_to_3wa",
    "daily_dates",
    "daily_fields",
    "daily_max_freezing_level",
    "day_is_complete",
    "detect_caaml_shape",
    "fake_address",
    "fetch_bulletins",
    "fetch_elevation",
    "fetch_forecast",
    "forecast_entry",
    "format_comment_as_html",
    "history_base_url",
    "hourly_rows_for_day",
    "merge_ndjson_records",
    "merge_slf_archive",
    "meteofrance_pdf_url",
    "normalise_bulletin_response",
    "normalise_slf_response",
    "normalise_slug",
    "parse_dpbra_xml",
    "parse_dt",
    "parse_dt_preserve_offset",
    "pdf_session",
    "read_slf_archive",
    "resolve_issued_at",
    "slf_pdf_url",
    "slug_to_region_id",
    "write_ndjson_archive",
    "write_slf_archive",
]
