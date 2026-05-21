# mf_bra_index_archive.py — Stage 1 of the Météo-France BRA backfill pipeline.
#
# Hits the public index endpoint (bra.YYYYMMDD.json) for each date in the
# configured range, classifies each response as ok / not_found / error:<reason>,
# and appends one NDJSON record per date to the output file.
#
# The script is resumable: dates already present in the output file are skipped.
# Off-season dates (outside the Nov–Apr window) are skipped without an HTTP
# request.  A 0.2 s delay between requests respects Météo-France's implicit
# rate limit.
"""Stage 1 — Météo-France BRA daily index archiver."""
