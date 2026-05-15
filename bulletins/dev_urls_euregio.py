"""
bulletins/dev_urls_euregio.py — Development-only URL routes for the EUREGIO mirror.

Mounted under ``/dev/euregio-mirror/`` from ``config/urls.py`` only when
``settings.DEBUG`` is true. Production never imports this module.

The mirror replicates the ALBINA CDN URL shape::

    /<date>/<date>_<region>_en_CAAMLv6.json

so that ``fetch_euregio_bulletins --source local-mirror`` can replay the
on-disk archive (``bulletins/local_mirrors/euregio_archive.ndjson``)
end-to-end through the production fetcher without any code change.

A separate URL module (rather than appending to ``bulletins.dev_urls``) avoids
the ``urls.W005`` duplicate-namespace warning that arises when the same app_name
is declared twice.
"""

from django.urls import re_path

from bulletins.dev_views import euregio_mirror

app_name = "dev_euregio"

# Matches /<date>/<date>_<region>_en_CAAMLv6.json where:
#   - <date> is YYYY-MM-DD
#   - <region> is an EAWS region code like AT-07, IT-32-BZ, IT-32-TN
#
# Named group ``region`` captures everything between the second ``_`` and
# ``_en_CAAMLv6.json`` in the filename segment.
_DATE_REGION_PATTERN = (
    r"(?P<date_str>\d{4}-\d{2}-\d{2})/"
    r"\d{4}-\d{2}-\d{2}_(?P<region>[A-Z]{2}-[\w-]+)_en_CAAMLv6\.json"
)

urlpatterns = [
    re_path(
        _DATE_REGION_PATTERN,
        euregio_mirror,
        name="euregio_mirror",
    ),
]
