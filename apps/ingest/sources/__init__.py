"""
ingest.sources — The providers' HTTP endpoints, one module per provider.

Plain ``requests`` calls that take a base URL (and a key where one is needed)
and return the provider's response, normalised. No database: what to do with
a bulletin or a forecast is the caller's business.
"""
