# build_aria2c_input.py — Stage 3 of the Météo-France BRA backfill pipeline.
#
# Reads bra_urls.csv (produced by Stage 2) and writes an aria2c input file
# (bra_downloads.txt) with --out directives that normalise filenames to
# BRA.{MASSIF}.{YYYY-MM-DD}.pdf regardless of which heures was published.
#
# If any URL row is skipped (status != 'ok'), the script exits with code 2
# so CI or shell pipelines can detect partial output.
#
# NOTE: deduplication of (massif, date) keeping only the latest heures was
# deliberately omitted.  aria2c will download all heures and the last one
# written wins on disk — this is the intended behaviour for re-published
# bulletins.  See README.md for rationale.
"""Stage 3 — Build aria2c input file from BRA URL CSV."""
