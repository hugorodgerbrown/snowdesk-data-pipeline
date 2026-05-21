# mf_bra_url_discover.py — Stage 2 of the Météo-France BRA backfill pipeline.
#
# Reads bra_indexes.ndjson (produced by Stage 1, or fetched on demand if absent)
# and expands each (massif, heures[]) pair into one CSV row per combination.
# Multiple heures entries per day (re-published bulletins) are each emitted as
# a separate row; the downloader will overwrite with the latest heures on disk.
#
# Output columns: massif, date, heures, url, status
"""Stage 2 — BRA URL discovery from index NDJSON to CSV."""
