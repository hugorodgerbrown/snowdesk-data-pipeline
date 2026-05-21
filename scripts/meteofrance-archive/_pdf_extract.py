# _pdf_extract.py — Column-aware pdfplumber extraction helpers.
#
# BRA PDFs use a two-column layout on page 1 (left: snowpack/stability narrative;
# right: weather summary + wind table).  Page 2 is a historical chart grid.
# These helpers use page.crop() to isolate regions before extracting text, which
# significantly reduces noise from the adjacent column bleeding into extractions.
"""Column-aware pdfplumber crop helpers for BRA PDF parsing."""
