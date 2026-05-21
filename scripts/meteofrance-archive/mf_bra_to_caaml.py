# mf_bra_to_caaml.py — Stage 5 of the Météo-France BRA backfill pipeline.
#
# Parses BRA PDF files and emits one NDJSON record per bulletin in CAAML 6.0
# GeoJSON Feature format.  Supports a --dry-run flag that prints a field-
# coverage report instead of writing the NDJSON file.
#
# Known limitations (tracked as follow-ups):
#   - Per-day historical danger ratings on page 2 are not parsed; only the
#     current-day rating is extracted from page 1.
#   - Snow-depth/cover series and fresh-snow series on page 2 are partially
#     extracted but may miss values when text rendering is fragmented.
#   - The SAT→problem-type mapping is an initial reasonable set; the official
#     MF alignment is a separate follow-up ticket.
#   - Page 2 position-based chart parsing is sensitive to layout drift across
#     massifs and seasons.  The --dry-run report helps spot regressions.
"""Stage 5 — Parse BRA PDFs and emit CAAML 6.0 NDJSON."""
