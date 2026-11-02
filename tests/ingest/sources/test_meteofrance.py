"""
tests/ingest/sources/test_meteofrance.py — Tests for apps.ingest.sources.meteofrance.

Moved from tests/bulletins/services/test_meteofrance_fetcher.py with the HTTP
code (SNOW-1249):
  - fetch_meteofrance_bulletin: HTTP success, 404 → None, HTTP error, live URL
    construction, missing API key error, file:// local mirror path, the
    session it is given (SNOW-1380).
  - dpbra_session: the key header, a connect-only retry, a refused connect
    retried then raised (SNOW-1380).
  - _read_local_mirror: file present → bytes, file absent → None.
  - resolve_base_url: live by default, mirror only when explicit.
  - meteofrance_pdf_url: 200-with-match, 200-without-match, 404, network
    error, empty massif — all return "" or correct URL.
"""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection
from urllib3.exceptions import NewConnectionError

from apps.ingest.sources.meteofrance import (
    CONNECT_BACKOFF_FACTOR,
    CONNECT_RETRIES,
    MF_USER_AGENT,
    _read_local_mirror,
    dpbra_session,
    fetch_meteofrance_bulletin,
    meteofrance_pdf_url,
    resolve_base_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "research"
    / "meteofrance"
    / "bulletins-2026-05-18"
)

# The ``bulletin_id`` the FR-01 sample document produces: massif, covered date,
# and @DATEDIFFUSION (2026-05-17T16:02:00 Paris → 14:02:00Z) as the publication
# stamp that separates the day's issues (SNOW-559).
_LIVE_ID = "FR-01-2026-05-18-20260517140200"


def _sample(filename: str) -> bytes:
    """Return raw bytes from the research sample directory."""
    return (_SAMPLE_DIR / filename).read_bytes()


def _mock_ok(content: bytes) -> MagicMock:
    """Build a mock requests.Response with status 200 and given content."""
    mock = MagicMock()
    mock.status_code = 200
    mock.content = content
    mock.raise_for_status.return_value = None
    return mock


def _mock_404() -> MagicMock:
    """Build a mock requests.Response with status 404."""
    mock = MagicMock()
    mock.status_code = 404
    mock.raise_for_status.return_value = None
    return mock


def _mock_http_error(status: int = 500) -> MagicMock:
    """Build a mock requests.Response that raises on raise_for_status."""
    mock = MagicMock()
    mock.status_code = status
    mock.raise_for_status.side_effect = requests.HTTPError(
        f"HTTP {status}", response=mock
    )
    return mock


# ---------------------------------------------------------------------------
# fetch_meteofrance_bulletin — HTTP path
# ---------------------------------------------------------------------------


class TestFetchMeteofrance:
    """fetch_meteofrance_bulletin handles HTTP and file:// paths."""

    @patch("apps.ingest.sources.meteofrance.requests.Session.get")
    def test_returns_bytes_on_200(self, mock_get: MagicMock) -> None:
        """A 200 response returns the content bytes."""
        mock_get.return_value = _mock_ok(b"<xml/>")
        result = fetch_meteofrance_bulletin(1, "https://api.example.com", "key123")
        assert result == b"<xml/>"

    @patch("apps.ingest.sources.meteofrance.requests.Session.get")
    def test_returns_none_on_404(self, mock_get: MagicMock) -> None:
        """A 404 response returns None (no bulletin today)."""
        mock_get.return_value = _mock_404()
        result = fetch_meteofrance_bulletin(1, "https://api.example.com", "key123")
        assert result is None

    @patch("apps.ingest.sources.meteofrance.requests.Session.get")
    def test_raises_on_http_error(self, mock_get: MagicMock) -> None:
        """A 500 response raises requests.HTTPError."""
        mock_get.return_value = _mock_http_error(500)
        with pytest.raises(requests.HTTPError):
            fetch_meteofrance_bulletin(1, "https://api.example.com", "key123")

    @patch("apps.ingest.sources.meteofrance.requests.Session.get")
    def test_url_uses_massif_id(self, mock_get: MagicMock) -> None:
        """The request URL includes the massif ID at the correct path."""
        mock_get.return_value = _mock_ok(b"<xml/>")
        fetch_meteofrance_bulletin(7, "https://api.example.com", "key")
        call_url = mock_get.call_args[0][0]
        assert "/massif/7/BRA" in call_url

    def test_uses_the_session_it_is_given(self) -> None:
        """A passed session makes the request; no other session is built."""
        session = MagicMock()
        session.get.return_value = _mock_ok(b"<xml/>")
        with patch("apps.ingest.sources.meteofrance.dpbra_session") as build:
            result = fetch_meteofrance_bulletin(
                3, "https://api.example.com", "key", session=session
            )
        assert result == b"<xml/>"
        build.assert_not_called()
        session.get.assert_called_once()
        assert "/massif/3/BRA" in session.get.call_args[0][0]

    def test_builds_a_dpbra_session_when_none_given(self) -> None:
        """A lone call goes through a keyed dpbra_session and closes it."""
        with patch("apps.ingest.sources.meteofrance.dpbra_session") as build:
            build.return_value.get.return_value = _mock_404()
            fetch_meteofrance_bulletin(1, "https://api.example.com", "my-key")
        build.assert_called_once_with("my-key")
        build.return_value.close.assert_called_once_with()

    def test_a_lone_call_closes_its_session_when_the_request_fails(self) -> None:
        """The one-off session is closed even when the request raises."""
        with patch("apps.ingest.sources.meteofrance.dpbra_session") as build:
            build.return_value.get.side_effect = requests.ConnectionError("refused")
            with pytest.raises(requests.ConnectionError):
                fetch_meteofrance_bulletin(1, "https://api.example.com", "my-key")
        build.return_value.close.assert_called_once_with()

    def test_a_given_session_is_left_open(self) -> None:
        """A session the caller passes is the caller's to close."""
        session = MagicMock()
        session.get.return_value = _mock_404()
        fetch_meteofrance_bulletin(1, "https://api.example.com", "k", session=session)
        session.close.assert_not_called()

    def test_raises_when_no_api_key_and_live_url(self) -> None:
        """An empty api_key with a live URL raises RuntimeError."""
        with pytest.raises(RuntimeError, match="METEOFRANCE_API_KEY"):
            fetch_meteofrance_bulletin(1, "https://api.example.com", "")


# ---------------------------------------------------------------------------
# dpbra_session — one keep-alive session with a bounded connect retry
# ---------------------------------------------------------------------------


class TestDpbraSession:
    """dpbra_session carries the key and retries a failed connect only (SNOW-1380)."""

    def test_sends_the_api_key_and_user_agent(self) -> None:
        """The key rides on every request as the apikey header."""
        session = dpbra_session("my-secret-key")
        assert session.headers["apikey"] == "my-secret-key"
        assert session.headers["User-Agent"] == MF_USER_AGENT

    def test_retries_connects_and_nothing_else(self) -> None:
        """The https adapter retries a connect three times, never a read or status."""
        adapter = dpbra_session("k").get_adapter("https://public-api.meteofrance.fr/")
        assert isinstance(adapter, HTTPAdapter)
        retry = adapter.max_retries
        assert retry.connect == CONNECT_RETRIES == 3
        assert retry.total == CONNECT_RETRIES
        assert retry.read == 0
        assert retry.status == 0
        assert retry.other == 0
        assert retry.backoff_factor == CONNECT_BACKOFF_FACTOR

    def test_a_refused_connect_is_retried_then_raises(self) -> None:
        """Every refused connect is retried; the last refusal is a ConnectionError.

        No network: urllib3's connect is replaced by a refusal, so the test
        counts the attempts the adapter makes (one plus three retries).
        """

        def refuse(conn: HTTPConnection) -> None:
            raise NewConnectionError(conn, "[Errno 111] Connection refused")

        with (
            patch("apps.ingest.sources.meteofrance.CONNECT_BACKOFF_FACTOR", 0),
            patch.object(
                HTTPConnection, "_new_conn", autospec=True, side_effect=refuse
            ) as connect,
        ):
            session = dpbra_session("k")
            with pytest.raises(requests.ConnectionError):
                fetch_meteofrance_bulletin(
                    18, "https://public-api.example", "k", session=session
                )
        assert connect.call_count == 1 + CONNECT_RETRIES

    @patch("apps.ingest.sources.meteofrance.requests.Session.get")
    def test_one_session_serves_many_fetches(self, mock_get: MagicMock) -> None:
        """Successive fetches reuse the session's connection pool."""
        mock_get.return_value = _mock_404()
        session = dpbra_session("k")
        for massif_id in (1, 2, 3):
            fetch_meteofrance_bulletin(
                massif_id, "https://api.example.com", "k", session=session
            )
        assert mock_get.call_count == 3


# ---------------------------------------------------------------------------
# _read_local_mirror
# ---------------------------------------------------------------------------


class TestReadLocalMirror:
    """_read_local_mirror reads files from a file:// directory."""

    def test_returns_bytes_when_file_exists(self) -> None:
        """When massif-001.xml is present, returns its bytes."""
        with tempfile.TemporaryDirectory() as tmp:
            massif_file = Path(tmp) / "massif-001.xml"
            massif_file.write_bytes(b"<test/>")
            result = _read_local_mirror(1, f"file://{tmp}")
            assert result == b"<test/>"

    def test_returns_none_when_file_absent(self) -> None:
        """Missing file for a massif returns None (mirrors HTTP 404 semantics)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _read_local_mirror(99, f"file://{tmp}")
            assert result is None

    def test_file_path_uses_three_digit_padding(self) -> None:
        """The file name is massif-{id:03d}.xml (zero-padded to 3 digits)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create massif-005.xml (not massif-5.xml)
            (Path(tmp) / "massif-005.xml").write_bytes(b"<padded/>")
            result = _read_local_mirror(5, f"file://{tmp}")
            assert result == b"<padded/>"

    def test_reads_a_percent_encoded_path(self, tmp_path: Path) -> None:
        """A mirror URI from ``Path.as_uri()`` reads a directory with a space."""
        mirror = tmp_path / "snowdesk dev"
        mirror.mkdir()
        (mirror / "massif-001.xml").write_bytes(b"<spaced/>")
        uri = mirror.as_uri()
        assert "%20" in uri
        assert _read_local_mirror(1, uri) == b"<spaced/>"


# ---------------------------------------------------------------------------
# _resolve_base_url — live by default, mirror only when explicit
# ---------------------------------------------------------------------------


class TestResolveBaseUrl:
    """_resolve_base_url never picks up the mirror setting implicitly."""

    @override_settings(
        METEOFRANCE_API_BASE_URL="https://api.example.com",
        METEOFRANCE_API_LOCAL_MIRROR_URL="file:///should/not/be/used",
    )
    def test_uses_live_when_base_url_none_even_if_mirror_set(self) -> None:
        """A stale mirror setting must not redirect a default (live) run."""
        assert resolve_base_url(None) == "https://api.example.com"

    @override_settings(
        METEOFRANCE_API_BASE_URL="https://api.example.com",
        METEOFRANCE_API_LOCAL_MIRROR_URL="",
    )
    def test_uses_live_when_mirror_empty(self) -> None:
        """With no mirror configured, the live setting is used."""
        assert resolve_base_url(None) == "https://api.example.com"

    @override_settings(METEOFRANCE_API_BASE_URL="https://api.example.com")
    def test_explicit_base_url_wins(self) -> None:
        """An explicit base_url argument always wins."""
        assert resolve_base_url("file:///explicit/mirror") == "file:///explicit/mirror"


# ---------------------------------------------------------------------------
# fetch_meteofrance_bulletin — local-mirror path
# ---------------------------------------------------------------------------


class TestFetchMeteofrancoLocalMirror:
    """fetch_meteofrance_bulletin reads from file:// without network I/O."""

    def test_returns_bytes_from_local_mirror(self) -> None:
        """A file:// base_url reads from the local directory, no HTTP call."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(b"<local/>")
            result = fetch_meteofrance_bulletin(1, f"file://{tmp}", "")
            assert result == b"<local/>"

    def test_no_api_key_required_for_local_mirror(self) -> None:
        """Empty api_key is fine for file:// mirrors."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-002.xml").write_bytes(b"<local/>")
            # Must not raise even though api_key is ""
            result = fetch_meteofrance_bulletin(2, f"file://{tmp}", "")
            assert result == b"<local/>"

    def test_mirror_never_touches_a_session(self) -> None:
        """A file:// read builds no session and makes no request (SNOW-1380)."""
        session = MagicMock()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("apps.ingest.sources.meteofrance.dpbra_session") as build,
        ):
            (Path(tmp) / "massif-003.xml").write_bytes(b"<local/>")
            result = fetch_meteofrance_bulletin(3, f"file://{tmp}", "", session=session)
        assert result == b"<local/>"
        build.assert_not_called()
        session.get.assert_not_called()


# ---------------------------------------------------------------------------


def _make_index_response(entries: list[dict]) -> MagicMock:
    """Build a mock session.get() response returning a JSON index list."""
    mock = MagicMock()
    mock.status_code = 200
    mock.ok = True
    mock.json.return_value = entries
    mock.raise_for_status.return_value = None
    return mock


def _make_session(get_return: MagicMock) -> MagicMock:
    """Build a mock requests.Session whose get() returns get_return."""
    session = MagicMock(spec=requests.Session)
    session.get.return_value = get_return
    return session


class TestMeteofrancePdfUrl:
    """Tests for meteofrance_pdf_url (network calls fully mocked)."""

    def test_returns_url_when_massif_matches(self) -> None:
        """Returns the PDF URL when the massif is found and heures is non-empty."""
        index = [
            {"massif": "CHABLAIS", "heures": ["20260115100000"]},
            {"massif": "MONT-BLANC", "heures": ["20260115100000"]},
        ]
        session = _make_session(_make_index_response(index))
        result = meteofrance_pdf_url("CHABLAIS", date(2026, 1, 15), session)
        assert result == (
            "https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA/"
            "BRA.CHABLAIS.20260115100000.pdf"
        )

    def test_picks_latest_heures_when_multiple(self) -> None:
        """When a massif has multiple heures, the last (sorted) entry is used."""
        index = [
            {"massif": "MONT-BLANC", "heures": ["20260115100000", "20260115153000"]},
        ]
        session = _make_session(_make_index_response(index))
        result = meteofrance_pdf_url("MONT-BLANC", date(2026, 1, 15), session)
        assert "20260115153000" in result
        assert "20260115100000" not in result

    def test_returns_empty_when_massif_not_in_index(self) -> None:
        """Returns '' when the massif is absent from the index."""
        index = [{"massif": "BELLEDONNE", "heures": ["20260115100000"]}]
        session = _make_session(_make_index_response(index))
        result = meteofrance_pdf_url("CHABLAIS", date(2026, 1, 15), session)
        assert result == ""

    def test_returns_empty_on_404(self) -> None:
        """Returns '' when the index endpoint returns 404 (off-season)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.ok = False
        session = _make_session(mock_resp)
        result = meteofrance_pdf_url("CHABLAIS", date(2026, 1, 15), session)
        assert result == ""

    def test_returns_empty_on_network_error(self) -> None:
        """Returns '' when a network-level exception is raised (fail-open)."""
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.RequestException("timeout")
        result = meteofrance_pdf_url("CHABLAIS", date(2026, 1, 15), session)
        assert result == ""

    def test_returns_empty_for_empty_massif_name(self) -> None:
        """Returns '' immediately when massif_name is empty."""
        session = MagicMock(spec=requests.Session)
        result = meteofrance_pdf_url("", date(2026, 1, 15), session)
        assert result == ""
        session.get.assert_not_called()

    def test_returns_empty_when_heures_is_empty_list(self) -> None:
        """Returns '' when the massif exists but heures is an empty list."""
        index = [{"massif": "CHABLAIS", "heures": []}]
        session = _make_session(_make_index_response(index))
        result = meteofrance_pdf_url("CHABLAIS", date(2026, 1, 15), session)
        assert result == ""

    def test_returns_empty_on_non_200_non_404(self) -> None:
        """Returns '' on an unexpected 5xx response (fail-open)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.ok = False
        session = _make_session(mock_resp)
        result = meteofrance_pdf_url("CHABLAIS", date(2026, 1, 15), session)
        assert result == ""
