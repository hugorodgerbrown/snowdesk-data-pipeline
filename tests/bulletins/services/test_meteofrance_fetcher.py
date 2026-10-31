"""
tests/bulletins/services/test_meteofrance_fetcher.py — Tests for the MeteoFrance fetcher.

Covers (the DPBRA fetch, the local mirror and the archive PDF lookup are
tested in tests/ingest/sources/test_meteofrance.py since SNOW-1249):
  - run_meteofrance_pipeline: dry-run no writes, force vs. dedup, delegated
    skip without failure count, translation error increments records_failed,
    HTTP error increments records_failed, on_fetched callback, PipelineRun
    lifecycle.
  - latest_meteofrance_date: returns None when DB empty, latest date otherwise.
  - meteofrance_stash_writer: merge, dedup, sort, atomic write.

DB tests use MicroRegionFactory to seed the regions referenced by test XMLs
(FR-01 = Chablais). HTTP calls are mocked via unittest.mock.patch.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.bulletins.models import PipelineRun
from apps.bulletins.services.apply import UnknownRegionError
from apps.bulletins.services.meteofrance_fetcher import (
    latest_meteofrance_date,
    meteofrance_stash_writer,
    run_meteofrance_pipeline,
)
from tests.factories import MicroRegionFactory

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
# run_meteofrance_pipeline — dry-run (no DB needed)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _no_pdf_index() -> Iterator[MagicMock]:
    """Stub the archive-PDF index lookup the pipeline makes per bulletin.

    ``run_meteofrance_pipeline`` opens its own ``requests.Session`` to resolve
    each bulletin's archive PDF URL. With only the XML fetch mocked, that
    lookup went to donneespubliques.meteofrance.fr for real and passed only
    because it fails open. ``None`` is the function's own "no index" answer.
    """
    with patch(
        "apps.ingest.sources.meteofrance.fetch_mf_index",
        return_value=None,
    ) as stub:
        yield stub


@pytest.mark.usefixtures("_no_pdf_index")
class TestRunMeteofrance:
    """run_meteofrance_pipeline: pipeline lifecycle and outcome counting."""

    @pytest.mark.django_db
    def test_dry_run_does_not_write_bulletins(self) -> None:
        """dry_run=True fetches, translates, but does not write to the DB."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.count() == 0
        assert run.records_created == 0

    @pytest.mark.django_db
    def test_delegated_massif_is_skipped_silently(self) -> None:
        """A delegated massif (massif-071) does not increment records_failed."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-071.xml").write_bytes(_sample("massif-071.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(71,),
                base_url=f"file://{tmp}",
            )

        assert run.records_failed == 0

    @pytest.mark.django_db
    def test_translation_error_increments_records_failed(self) -> None:
        """Invalid XML increments records_failed on the PipelineRun."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(b"<invalid xml<<>")
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        assert run.records_failed == 1

    @pytest.mark.django_db
    def test_http_error_increments_records_failed(self) -> None:
        """An HTTP error for a massif increments records_failed."""
        with patch(
            "apps.ingest.sources.meteofrance.fetch_meteofrance_bulletin"
        ) as mock_fetch:
            mock_fetch.side_effect = requests.HTTPError("500 Server Error")
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1,),
                base_url="https://api.example.com",
            )

        assert run.records_failed == 1
        # SNOW-1228: a run with a failed record finishes PARTIAL.
        run.refresh_from_db()
        assert run.status == PipelineRun.Status.PARTIAL

    @pytest.mark.django_db
    def test_on_fetched_callback_called_per_bulletin(self) -> None:
        """on_fetched is invoked once for each successfully translated bulletin."""
        collected: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            (Path(tmp) / "massif-064.xml").write_bytes(_sample("massif-064.xml"))
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=True,
                massif_ids=(1, 64),
                base_url=f"file://{tmp}",
                on_fetched=collected.append,
            )

        assert len(collected) == 2
        ids = {r["bulletinID"] for r in collected}
        assert "FR-01-2026-05-18-20260517140200" in ids
        assert "FR-64-2026-05-18-20260517135700" in ids

    @pytest.mark.django_db
    def test_creates_bulletin_for_known_region(self) -> None:
        """A bulletin whose region is seeded in the DB is persisted."""
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.filter(bulletin_id=_LIVE_ID).exists()
        assert run.records_created == 1

    @pytest.mark.django_db
    def test_dedup_skips_existing_bulletin(self) -> None:
        """A bulletin that already exists in the DB is skipped (no force)."""
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            # First run creates the bulletin.
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )
            # Second run should skip it (force=False).
            run2 = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from apps.bulletins.models import Bulletin

        # Still only one bulletin in the DB.
        assert Bulletin.objects.filter(bulletin_id=_LIVE_ID).count() == 1
        assert run2.records_created == 0

    @pytest.mark.django_db
    def test_idempotency_second_commit_run_is_noop(self) -> None:
        """Second --commit run with no --force produces records_updated=0 and records_created=0.

        Regression test for SNOW-259: confirms that re-running the pipeline
        over a day already in the DB leaves no new rows and no updates.
        """
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            # First run: creates the bulletin.
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )
            # Second run: must be a no-op (no force).
            run2 = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        assert run2.records_created == 0
        assert run2.records_updated == 0

    @pytest.mark.django_db
    def test_delegated_region_counted_separately_from_failures(self) -> None:
        """Delegated massifs (e.g. massif 71 Andorre) do not increment records_failed.

        Regression test for SNOW-259: a delegated-region skip must be a clean
        skip, not a pipeline failure — so operator dashboards can distinguish
        "expected delegations" from "unexpected errors".
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-071.xml").write_bytes(_sample("massif-071.xml"))
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(71,),
                base_url=f"file://{tmp}",
            )

        # The delegation is a clean skip — records_failed must be zero.
        assert run.records_failed == 0

    @pytest.mark.django_db
    def test_local_mirror_produces_bulletins_without_http(self) -> None:
        """The --local-mirror (file://) path loads XML fixtures with no HTTP calls.

        Regression test for SNOW-259: verifies that pointing base_url at the
        bundled research fixtures in docs/research/meteofrance/bulletins-2026-05-18/
        produces a persisted bulletin and no network I/O.
        """
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        mirror_url = f"file://{_SAMPLE_DIR}"
        with patch("apps.ingest.sources.meteofrance.requests.get") as mock_get:
            run = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=mirror_url,
            )

        # No HTTP call should have been made.
        mock_get.assert_not_called()

        from apps.bulletins.models import Bulletin

        assert Bulletin.objects.filter(bulletin_id=_LIVE_ID).exists()
        assert run.records_created == 1

    @pytest.mark.django_db
    def test_translated_bulletin_has_custom_data_massif_slug(self) -> None:
        """Translator output carries customData.MF.massif as upper-case slug.

        Regression for SNOW-295 Bug 1: the ingest path was reading
        regions[0]["name"] (title-case, e.g. "Chablais") as the massif slug
        instead of the canonical upper-case form required by the MF index
        endpoint ("CHABLAIS").  This test verifies that after the fix the
        persisted raw_data carries customData.MF.massif with the correct
        upper-case value.
        """
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        from apps.bulletins.models import Bulletin

        b = Bulletin.objects.get(bulletin_id=_LIVE_ID)
        raw_props = (b.raw_data or {}).get("properties", {})
        massif_slug: str = (
            raw_props.get("customData", {}).get("MF", {}).get("massif", "")
        )
        region_name: str = (raw_props.get("regions") or [{}])[0].get("name", "")
        # regions[0].name carries the title-case display name.
        assert region_name == "Chablais"
        # customData.MF.massif must be the upper-case slug used by the index.
        assert massif_slug == "CHABLAIS"

    @pytest.mark.django_db
    def test_walk_raising_mid_iteration_fails_the_run(self) -> None:
        """An exception from the provider walk marks the run FAILED, not raised.

        The walk yields the one real bulletin, then raises. The run must be
        returned FAILED with the exception's message stored, so the
        ``fetch_bulletins`` command can exit non-zero on it.
        """
        from apps.ingest.public import fetch_bulletins as real_fetch_bulletins

        def _walk_then_raise(*args: object, **kwargs: object) -> Iterator[object]:
            """Yield the real walk's items, then fail as a broken walk would."""
            yield from real_fetch_bulletins(*args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("walk broke mid-iteration")

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            with patch(
                "apps.bulletins.services.meteofrance_fetcher.fetch_bulletins",
                side_effect=_walk_then_raise,
            ):
                run = run_meteofrance_pipeline(
                    date(2026, 5, 18),
                    date(2026, 5, 18),
                    triggered_by="test",
                    dry_run=True,
                    massif_ids=(1,),
                    base_url=f"file://{tmp}",
                )

        run.refresh_from_db()
        assert run.status == PipelineRun.Status.FAILED
        assert run.error_message == "walk broke mid-iteration"
        assert run.finished_at is not None

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(UnknownRegionError("FR-01 not seeded"), id="unknown-region"),
            pytest.param(RuntimeError("database fell over"), id="generic"),
        ],
    )
    def test_apply_failure_counts_record_failed_and_finishes_partial(
        self, error: Exception, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bulletin whose apply raises is counted failed; the run ends PARTIAL.

        Both ``except`` branches of ``_process_bulletin`` — the
        ``UnknownRegionError`` one and the catch-all — persist
        ``records_failed`` and return the failed outcome, and the run carries
        on to finish PARTIAL rather than FAILED.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            with (
                patch(
                    "apps.bulletins.services.meteofrance_fetcher.apply_bulletin",
                    side_effect=error,
                ) as mock_apply,
                caplog.at_level(
                    logging.INFO,
                    logger="apps.bulletins.services.meteofrance_fetcher",
                ),
            ):
                run = run_meteofrance_pipeline(
                    date(2026, 5, 18),
                    date(2026, 5, 18),
                    triggered_by="test",
                    dry_run=False,
                    massif_ids=(1,),
                    base_url=f"file://{tmp}",
                )

        mock_apply.assert_called_once()
        assert mock_apply.call_args.args[0].bulletin_id == _LIVE_ID
        run.refresh_from_db()
        assert run.records_failed == 1
        assert run.records_created == 0
        assert run.status == PipelineRun.Status.PARTIAL
        # The failed outcome is counted in the run's finish line.
        assert "0 created, 0 updated, 0 skipped, 0 delegated, 1 failed" in caplog.text


# ---------------------------------------------------------------------------
# latest_meteofrance_date
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_no_pdf_index")
class TestLatestMeteofrancoDate:
    """latest_meteofrance_date returns None or the most recent MF bulletin date."""

    def test_returns_none_when_no_bulletins(self) -> None:
        """Empty DB returns None."""
        result = latest_meteofrance_date()
        assert result is None

    def test_returns_latest_date_when_bulletins_exist(self) -> None:
        """Returns the valid_from date of the most recent MeteoFrance bulletin."""
        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        result = latest_meteofrance_date()
        assert result is not None
        # massif-001 bulletin is valid from 2026-05-17T16:00 Paris (UTC+2)
        # = 2026-05-17T14:00:00Z, so .date() == 2026-05-17
        assert result == date(2026, 5, 17)

    def test_ignores_non_meteofrance_bulletins(self) -> None:
        """Bulletins whose render_model.source != "meteofrance" are not counted."""
        from apps.bulletins.models import Bulletin, PipelineRun

        run = PipelineRun.objects.create(triggered_by="test")
        Bulletin.objects.create(
            bulletin_id="SLF-001",
            lang="en",
            issued_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
            valid_to=datetime(2026, 5, 19, 0, 0, tzinfo=UTC),
            pipeline_run=run,
            raw_data={"type": "Feature", "geometry": None, "properties": {}},
            render_model={},
            render_model_version=0,
        )
        result = latest_meteofrance_date()
        assert result is None


# ---------------------------------------------------------------------------
# meteofrance_stash_writer
# ---------------------------------------------------------------------------


class TestMeteofranceStashWriter:
    """meteofrance_stash_writer merges and dedupes records into an NDJSON archive."""

    def test_creates_new_archive_when_path_absent(self) -> None:
        """A non-existent path is created with the supplied records."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "archive.ndjson"
            records = [
                {
                    "bulletinID": "FR-01-2026-05-18",
                    "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                }
            ]
            count = meteofrance_stash_writer(records, path)

            assert count == 1
            assert path.exists()
            lines = [json.loads(ln) for ln in path.read_text().splitlines()]
            assert lines[0]["bulletinID"] == "FR-01-2026-05-18"

    def test_merges_new_records_with_existing(self) -> None:
        """New records are merged with existing archive contents."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            existing = {
                "bulletinID": "FR-01-2026-05-17",
                "validTime": {"startTime": "2026-05-16T14:00:00Z"},
            }
            path.write_text(json.dumps(existing) + "\n")

            new_record = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
            }
            count = meteofrance_stash_writer([new_record], path)

            assert count == 2

    def test_dedupes_by_bulletin_id(self) -> None:
        """A new record with the same bulletinID replaces the existing one."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            old = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                "v": 1,
            }
            path.write_text(json.dumps(old) + "\n")

            updated = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                "v": 2,
            }
            count = meteofrance_stash_writer([updated], path)

            assert count == 1
            lines = [json.loads(ln) for ln in path.read_text().splitlines()]
            assert lines[0]["v"] == 2

    def test_sorted_ascending_by_valid_time(self) -> None:
        """Archive is sorted ascending by validTime.startTime."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            r1 = {
                "bulletinID": "FR-01-2026-05-18",
                "validTime": {"startTime": "2026-05-17T14:00:00Z"},
            }
            r2 = {
                "bulletinID": "FR-01-2026-05-17",
                "validTime": {"startTime": "2026-05-16T14:00:00Z"},
            }
            meteofrance_stash_writer([r1, r2], path)

            lines = [json.loads(ln) for ln in path.read_text().splitlines()]
            assert lines[0]["bulletinID"] == "FR-01-2026-05-17"
            assert lines[1]["bulletinID"] == "FR-01-2026-05-18"

    def test_returns_total_record_count(self) -> None:
        """Return value is the total archive size after the merge."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.ndjson"
            records = [
                {
                    "bulletinID": f"FR-0{i}-2026-05-18",
                    "validTime": {"startTime": "2026-05-17T14:00:00Z"},
                }
                for i in range(1, 6)
            ]
            count = meteofrance_stash_writer(records, path)

            assert count == 5


# ---------------------------------------------------------------------------
# _meteofrance_pdf_url
@pytest.mark.django_db
@pytest.mark.usefixtures("_no_pdf_index")
class TestReissuedBulletin:
    """A re-issue with a new @DATEDIFFUSION must be stored, not skipped.

    The live fetcher short-circuits on ``Bulletin.objects.filter(bulletin_id=…)
    .exists()``.  Under the old ``FR-{NN}-{covered date}`` id that meant the
    day's second issue was silently dropped and a massif was never updated after
    the first fetch of the day — no warning, no ``records_failed`` (SNOW-559).
    """

    def _reissued_xml(self) -> bytes:
        """Return the FR-01 sample with a later @DATEDIFFUSION.

        Returns:
            XML bytes identical to ``massif-001.xml`` but diffused at 07:30
            rather than 16:02, i.e. the following morning's refresh.

        """
        original = _sample("massif-001.xml").decode("utf-8")
        assert 'DATEDIFFUSION="2026-05-17T16:02:00"' in original
        return original.replace(
            'DATEDIFFUSION="2026-05-17T16:02:00"',
            'DATEDIFFUSION="2026-05-18T07:30:00"',
        ).encode("utf-8")

    def test_reissue_creates_a_second_row(self) -> None:
        """Both issues of one massif-day coexist."""
        from apps.bulletins.models import Bulletin

        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )
            # The morning refresh replaces the file at the same URL.
            (Path(tmp) / "massif-001.xml").write_bytes(self._reissued_xml())
            second = run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        assert second.records_created == 1
        assert (
            Bulletin.objects.filter(bulletin_id__startswith="FR-01-2026-05-18").count()
            == 2
        )

    def test_reissue_id_carries_its_own_stamp(self) -> None:
        """The second row is keyed by the re-issue's own publication instant."""
        from apps.bulletins.models import Bulletin

        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(self._reissued_xml())
            run_meteofrance_pipeline(
                date(2026, 5, 18),
                date(2026, 5, 18),
                triggered_by="test",
                dry_run=False,
                massif_ids=(1,),
                base_url=f"file://{tmp}",
            )

        # 2026-05-18T07:30:00 Paris (CEST, UTC+2) → 05:30:00Z.
        assert Bulletin.objects.filter(
            bulletin_id="FR-01-2026-05-18-20260518053000"
        ).exists()

    def test_identical_refetch_is_still_skipped(self) -> None:
        """Re-fetching the same issue remains a no-op.

        The publication stamp makes the id idempotent for an unchanged bulletin,
        so the existing skip is still correct — it just no longer swallows
        genuine re-issues.
        """
        from apps.bulletins.models import Bulletin

        MicroRegionFactory.create(region_id="FR-01", name="Chablais")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "massif-001.xml").write_bytes(_sample("massif-001.xml"))
            for _ in range(2):
                last = run_meteofrance_pipeline(
                    date(2026, 5, 18),
                    date(2026, 5, 18),
                    triggered_by="test",
                    dry_run=False,
                    massif_ids=(1,),
                    base_url=f"file://{tmp}",
                )

        assert last.records_created == 0
        assert last.records_updated == 0
        assert Bulletin.objects.count() == 1
