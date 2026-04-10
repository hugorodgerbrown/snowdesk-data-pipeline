"""
pipeline/admin.py — Django admin registrations for pipeline models.

Provides list views with key fields and read-only display of timing and
error information so that operators can inspect pipeline runs, regions,
and bulletins without needing direct database access.
"""

import json
import logging
from datetime import date

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Bulletin, PipelineRun, Region, RegionBulletin
from .services.data_fetcher import run_pipeline
from .utils import html_to_markdown

logger = logging.getLogger(__name__)


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    """Admin view for PipelineRun."""

    list_display = [
        "id",
        "status",
        "triggered_by",
        "started_at",
        "finished_at",
        "records_created",
        "records_updated",
    ]
    list_filter = ["status", "triggered_by"]
    readonly_fields = [
        "started_at",
        "finished_at",
        "status",
        "records_created",
        "records_updated",
        "error_message",
    ]
    ordering = ["-started_at"]


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    """Admin view for Region."""

    list_display = ["region_id", "name", "slug", "updated_at"]
    list_filter = []
    search_fields = ["region_id", "name"]
    ordering = ["region_id"]
    readonly_fields = ["id", "slug", "created_at", "updated_at"]


class RegionBulletinInline(admin.TabularInline):
    """Inline display of regions on the Bulletin admin page."""

    model = RegionBulletin
    extra = 0
    readonly_fields = ["region", "region_name_at_time", "created_at"]
    verbose_name = "Bulletin Region"


@admin.register(Bulletin)
class BulletinAdmin(admin.ModelAdmin):
    """Admin view for Bulletin."""

    list_display = [
        "bulletin_id",
        "issued_at",
        "valid_from",
        "valid_to",
        "lang",
        "unscheduled",
        "updated_at",
    ]
    list_filter = ["lang", "unscheduled", "issued_at"]
    search_fields = ["bulletin_id"]
    ordering = ["-issued_at"]
    readonly_fields = [
        "bulletin_id",
        "issued_at",
        "valid_from",
        "valid_to",
        "next_update",
        "lang",
        "unscheduled",
        "danger_ratings",
        "avalanche_problems",
        "weather_forecast",
        "weather_review",
        "snowpack_structure",
        "tendency",
        "raw_data_pretty",
        "created_at",
        "updated_at",
    ]
    inlines = [RegionBulletinInline]
    exclude = ["raw_data", "pipeline_run"]

    BACKFILL_START = date(2025, 12, 1)

    def get_urls(self) -> list[URLPattern]:
        """Add a custom URL for triggering the season backfill."""
        custom_urls = [
            path(
                "backfill/",
                self.admin_site.admin_view(self.backfill_view),
                name="pipeline_bulletin_backfill",
            ),
        ]
        return custom_urls + super().get_urls()

    def backfill_view(self, request: HttpRequest) -> HttpResponseRedirect:
        """
        Handle the backfill button POST.

        Runs the data pipeline from BACKFILL_START to today and redirects
        back to the changelist with a success or error message.
        """
        if request.method != "POST":
            self.message_user(request, "Invalid request method.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:pipeline_bulletin_changelist"))

        start = self.BACKFILL_START
        end = date.today()

        logger.info("Admin backfill triggered: %s to %s", start, end)
        try:
            run = run_pipeline(
                start=start,
                end=end,
                triggered_by="admin backfill",
                dry_run=False,
                force=False,
            )
        except Exception:
            logger.exception("Admin backfill failed")
            self.message_user(request, "Backfill failed — check logs.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:pipeline_bulletin_changelist"))

        if run.status == PipelineRun.Status.FAILED:
            self.message_user(
                request,
                f"Backfill run #{run.pk} failed: {run.error_message}",
                messages.ERROR,
            )
        else:
            self.message_user(
                request,
                f"Backfill complete — {run.records_created} created, "
                f"{run.records_updated} updated.",
                messages.SUCCESS,
            )
        return HttpResponseRedirect(reverse("admin:pipeline_bulletin_changelist"))

    def _get_properties(self, obj: Bulletin) -> dict:
        """
        Extract the CAAML properties dict from the GeoJSON raw_data envelope.

        Args:
            obj: The Bulletin instance.

        Returns:
            The properties dict, or an empty dict if not present.
        """
        return obj.raw_data.get("properties", {}) if obj.raw_data else {}

    def _render_comment(self, text: str | None) -> str:
        """
        Convert an HTML comment to Markdown and render in a styled block.

        Strips HTML tags, converts headings/lists/bold/italic to Markdown,
        and displays the result in a scrollable container. Returns a dash
        if the input is empty.

        Args:
            text: The raw HTML comment text from the CAAML bulletin.

        Returns:
            An HTML string for the admin detail view.
        """
        if not text:
            return "—"
        markdown = html_to_markdown(text)
        if not markdown:
            return "—"
        return format_html(
            '<pre style="max-height:300px;overflow:auto;background:#f5f5f5;'
            "padding:0.75rem;border-radius:4px;"
            "line-height:1.5;white-space:pre-wrap;word-break:break-word"
            '">{}</pre>',
            markdown,
        )

    # Danger level display names and colour coding.
    _DANGER_COLOURS: dict[str, tuple[str, str]] = {
        "low": ("#d1fae5", "#065f46"),
        "moderate": ("#fef3c7", "#92400e"),
        "considerable": ("#fed7aa", "#9a3412"),
        "high": ("#fee2e2", "#991b1b"),
        "very_high": ("#fca5a5", "#7f1d1d"),
    }

    @staticmethod
    def _format_danger_level(value: str) -> str:
        """
        Capitalise and clean up a CAAML danger level value for display.

        Args:
            value: Raw danger level string (e.g. "very_high").

        Returns:
            Human-readable label (e.g. "Very high").
        """
        return value.replace("_", " ").capitalize()

    @staticmethod
    def _format_time_period(value: str) -> str:
        """
        Format a CAAML validTimePeriod value for display.

        Args:
            value: Raw time period string (e.g. "all_day").

        Returns:
            Human-readable label (e.g. "All day").
        """
        return value.replace("_", " ").capitalize()

    @staticmethod
    def _format_elevation(elevation: dict | None) -> str:
        """
        Format an elevation object to a readable string.

        Args:
            elevation: Dict with optional "lowerBound" and "upperBound" keys.

        Returns:
            A string like "Above 2000m", "Below 2400m", or "All elevations".
        """
        if not elevation:
            return "All elevations"
        lower = elevation.get("lowerBound")
        upper = elevation.get("upperBound")
        if lower and upper:
            return f"{lower}m – {upper}m"
        if lower:
            return f"Above {lower}m"
        if upper:
            return f"Below {upper}m"
        return "All elevations"

    @admin.display(description="Danger ratings")
    def danger_ratings(self, obj: Bulletin) -> str:
        """
        Render danger ratings as a colour-coded HTML table.

        Shows the danger level, time period, and elevation for each
        rating entry in the bulletin.
        """
        props = self._get_properties(obj)
        ratings = props.get("dangerRatings", [])
        if not ratings:
            return "—"

        rows = []
        for r in ratings:
            level = r.get("mainValue", "unknown")
            period = r.get("validTimePeriod", "all_day")
            elevation = r.get("elevation")
            bg, fg = self._DANGER_COLOURS.get(level, ("#f3f4f6", "#374151"))
            rows.append(
                f"<tr>"
                f'<td style="padding:6px 12px">'
                f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                f"border-radius:4px;font-weight:600;"
                f'text-transform:uppercase">{self._format_danger_level(level)}</span>'
                f"</td>"
                f'<td style="padding:6px 12px">{self._format_time_period(period)}</td>'
                f'<td style="padding:6px 12px">{self._format_elevation(elevation)}</td>'
                f"</tr>"
            )

        table = (
            '<table style="border-collapse:collapse;">'
            '<thead><tr style="border-bottom:2px solid #d1d5db">'
            '<th style="padding:6px 12px;text-align:left">Level</th>'
            '<th style="padding:6px 12px;text-align:left">Period</th>'
            '<th style="padding:6px 12px;text-align:left">Elevation</th>'
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
        return mark_safe(table)  # noqa: S308 — content is built from escaped data

    @admin.display(description="Avalanche problems")
    def avalanche_problems(self, obj: Bulletin) -> str:
        """
        Render avalanche problems as an HTML table.

        Shows the problem type, danger level, time period, elevation,
        aspects, and any comment text (converted from HTML to Markdown).
        """
        props = self._get_properties(obj)
        problems = props.get("avalancheProblems", [])
        if not problems:
            return "—"

        rows = []
        for p in problems:
            problem_type = (
                p.get("problemType", "unknown").replace("_", " ").capitalize()
            )
            level = p.get("dangerRatingValue", "")
            period = p.get("validTimePeriod", "all_day")
            elevation = p.get("elevation")
            aspects = ", ".join(p.get("aspects", []))
            comment = p.get("comment", "")

            bg, fg = self._DANGER_COLOURS.get(level, ("#f3f4f6", "#374151"))
            level_badge = (
                f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                f"border-radius:4px;font-weight:600;"
                f'text-transform:uppercase">{self._format_danger_level(level)}</span>'
                if level
                else "—"
            )

            comment_html = ""
            if comment:
                md = html_to_markdown(comment)
                if md:
                    comment_html = (
                        f'<div style="margin-top:4px;color:#6b7280">{md}</div>'
                    )

            rows.append(
                f'<tr style="border-bottom:1px solid #e5e7eb;vertical-align:top">'
                f'<td style="padding:8px 12px;font-weight:600">{problem_type}</td>'
                f'<td style="padding:8px 12px">{level_badge}</td>'
                f'<td style="padding:8px 12px">{self._format_time_period(period)}</td>'
                f'<td style="padding:8px 12px">{self._format_elevation(elevation)}</td>'
                f'<td style="padding:8px 12px">{aspects or "—"}</td>'
                f'<td style="padding:8px 12px">{comment_html or "—"}</td>'
                f"</tr>"
            )

        table = (
            '<table style="border-collapse:collapse;width:100%">'
            '<thead><tr style="border-bottom:2px solid #d1d5db">'
            '<th style="padding:6px 12px;text-align:left">Problem</th>'
            '<th style="padding:6px 12px;text-align:left">Level</th>'
            '<th style="padding:6px 12px;text-align:left">Period</th>'
            '<th style="padding:6px 12px;text-align:left">Elevation</th>'
            '<th style="padding:6px 12px;text-align:left">Aspects</th>'
            '<th style="padding:6px 12px;text-align:left">Comment</th>'
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
        return mark_safe(table)  # noqa: S308 — content is built from escaped data

    @admin.display(description="Weather forecast")
    def weather_forecast(self, obj: Bulletin) -> str:
        """Extract and render the weather forecast comment."""
        props = self._get_properties(obj)
        forecast = props.get("weatherForecast", {})
        return self._render_comment(forecast.get("comment") if forecast else None)

    @admin.display(description="Weather review")
    def weather_review(self, obj: Bulletin) -> str:
        """Extract and render the weather review comment."""
        props = self._get_properties(obj)
        review = props.get("weatherReview", {})
        return self._render_comment(review.get("comment") if review else None)

    @admin.display(description="Snowpack structure")
    def snowpack_structure(self, obj: Bulletin) -> str:
        """Extract and render the snowpack structure comment."""
        props = self._get_properties(obj)
        snowpack = props.get("snowpackStructure", {})
        return self._render_comment(snowpack.get("comment") if snowpack else None)

    @admin.display(description="Tendency")
    def tendency(self, obj: Bulletin) -> str:
        """Extract and render tendency comments (may be a list)."""
        props = self._get_properties(obj)
        tendency_data = props.get("tendency", [])
        if not tendency_data:
            return "—"
        comments = [
            t.get("comment", "")
            for t in tendency_data
            if isinstance(t, dict) and t.get("comment")
        ]
        return self._render_comment("\n\n".join(comments) if comments else None)

    @admin.display(description="Raw data")
    def raw_data_pretty(self, obj: Bulletin) -> str:
        """Render raw_data as syntax-highlighted, indented JSON."""
        formatted = json.dumps(obj.raw_data, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:400px;overflow:auto;background:#f5f5f5;'
            "padding:0.75rem;border-radius:4px;"
            'white-space:pre-wrap;word-break:break-word">{}</pre>',
            formatted,
        )
