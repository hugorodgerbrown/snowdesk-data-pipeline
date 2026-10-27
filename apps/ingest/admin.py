"""
apps/ingest/admin.py — Django admin registration for the ingest app.

One registration, ``SourceBulletinAdmin``. A ``SourceBulletin`` is what a
provider published, so the admin is a read-only window on it: nothing can be
added, edited or deleted here. A wrong row is corrected at the provider, or
by re-fetching with ``fetch_bulletins --force``, never by hand — a hand edit
would be served to every feed client as if the provider had said it.
"""

import json

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString

from .models import SourceBulletin


@admin.register(SourceBulletin)
class SourceBulletinAdmin(admin.ModelAdmin[SourceBulletin]):
    """Read-only admin for stored provider bulletins."""

    list_display = ("bulletin_id", "provider", "fetched_at", "updated_at")
    list_filter = ("provider",)
    search_fields = ("bulletin_id",)
    date_hierarchy = "fetched_at"
    ordering = ("-updated_at",)
    readonly_fields = (
        "uuid",
        "bulletin_id",
        "provider",
        "pdf_url",
        "content_hash",
        "fetched_at",
        "created_at",
        "updated_at",
        "caaml_pretty",
    )
    exclude = ("caaml",)

    @admin.display(description="CAAML")
    def caaml_pretty(self, obj: SourceBulletin) -> SafeString:
        """Return the CAAML as indented JSON, escaped by ``format_html``."""
        return format_html("<pre>{}</pre>", json.dumps(obj.caaml, indent=2))

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Refuse: rows come from a provider fetch only."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: SourceBulletin | None = None
    ) -> bool:
        """Refuse edits; the change view still renders, read-only."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: SourceBulletin | None = None
    ) -> bool:
        """Refuse: a row is the record of what a provider said."""
        return False
