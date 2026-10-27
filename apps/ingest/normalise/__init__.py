"""
ingest.normalise — Pure functions from a provider's response to Snowdesk's shape.

No database, no network, no settings: each function takes what a provider
sent and returns CAAML v6 bulletin dicts or Open-Meteo day records. The
fetchers call these; so does anything replaying a stored response.
"""
