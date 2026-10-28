"""
apps/ingest/api — What ingest serves: the bulletins feed and the paging rule.

``paging`` is the keyset paging every data feed shares; ``bulletins`` is the
``bulletins`` feed, read from ``SourceBulletin`` (SNOW-1250). There is no view
here: the host mounts these behind its own authentication
(``apps/data_feed/views.py``, ``require_feed_token``).
"""
