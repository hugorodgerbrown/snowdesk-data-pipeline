# tests/bulletins/services/test_meteofrance_identity.py — Tests for the
# Météo-France bulletin identity scheme.
#
# The publication stamp is the whole point of the scheme: without it the two
# issues Météo-France publishes for one massif-day share an id, and the archive
# loader overwrites one with the other while the live fetcher skips the second
# outright (SNOW-559).  These tests pin the grammar both ingest paths build, so
# a change on one side cannot silently diverge from the other.
"""Tests for apps.bulletins.services.meteofrance_identity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from apps.bulletins.services.meteofrance_identity import (
    BULLETIN_ID_RE,
    build_bulletin_id,
    compact_publication_stamp,
)


class TestCompactPublicationStamp:
    """Tests for compact_publication_stamp()."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-02-13T07:44:07Z", "20260213074407"),
            ("2026-02-13T07:44:07+00:00", "20260213074407"),
            ("2026-02-12T15:27:06Z", "20260212152706"),
        ],
    )
    def test_parses_utc_strings(self, value: str, expected: str) -> None:
        """A UTC ISO-8601 string becomes the compact form.

        Args:
            value: The input timestamp string.
            expected: The expected 14-character stamp.

        """
        assert compact_publication_stamp(value) == expected

    def test_converts_an_offset_to_utc(self) -> None:
        """An offset-aware value is normalised to UTC before formatting.

        Paris local 16:02 in CEST is 14:02Z, and the id must carry the UTC form
        so the live and archive paths cannot disagree on the same instant.
        """
        paris_cest = timezone(timedelta(hours=2))
        value = datetime(2026, 5, 17, 16, 2, 0, tzinfo=paris_cest)

        assert compact_publication_stamp(value) == "20260517140200"

    def test_treats_a_naive_value_as_utc(self) -> None:
        """A naive datetime is assumed UTC — every producer here writes UTC."""
        assert (
            compact_publication_stamp(datetime(2026, 2, 13, 7, 44, 7))
            == "20260213074407"
        )

    def test_accepts_a_datetime(self) -> None:
        """A tz-aware datetime is accepted as well as a string."""
        value = datetime(2026, 2, 13, 7, 44, 7, tzinfo=UTC)

        assert compact_publication_stamp(value) == "20260213074407"

    @pytest.mark.parametrize("value", [None, "", "not-a-timestamp", 12345, []])
    def test_returns_none_for_unusable_values(self, value: object) -> None:
        """An absent or unparseable value returns None rather than guessing.

        Args:
            value: An input the function cannot use.

        """
        assert compact_publication_stamp(value) is None


class TestBuildBulletinId:
    """Tests for build_bulletin_id()."""

    def test_builds_the_canonical_form(self) -> None:
        """The id is region, covered date, then the publication stamp."""
        result = build_bulletin_id("FR-02", "2026-02-13", "2026-02-13T07:44:07Z")

        assert result == "FR-02-2026-02-13-20260213074407"

    def test_the_two_issues_of_a_day_get_different_ids(self) -> None:
        """The previous-evening and morning issues no longer collide.

        These are the real timestamps from the ticket's own example, ARAVIS
        covering 2026-02-13.
        """
        evening = build_bulletin_id("FR-02", "2026-02-13", "2026-02-12T15:27:06Z")
        morning = build_bulletin_id("FR-02", "2026-02-13", "2026-02-13T07:44:07Z")

        assert evening != morning
        assert evening == "FR-02-2026-02-13-20260212152706"
        assert morning == "FR-02-2026-02-13-20260213074407"

    def test_is_stable_for_the_same_inputs(self) -> None:
        """Re-deriving an id yields the same value, so upserts are idempotent."""
        args = ("FR-08", "2026-05-18", "2026-05-17T13:37:00Z")

        assert build_bulletin_id(*args) == build_bulletin_id(*args)

    def test_returns_none_without_a_publication_time(self) -> None:
        """A missing timestamp yields None so the caller can fail the record.

        Emitting an id without the stamp would reintroduce the collision this
        scheme exists to prevent, so there is deliberately no fallback.
        """
        assert build_bulletin_id("FR-02", "2026-02-13", None) is None
        assert build_bulletin_id("FR-02", "2026-02-13", "rubbish") is None


class TestBulletinIdPattern:
    """BULLETIN_ID_RE recognises ids already on the new grammar."""

    @pytest.mark.parametrize(
        "bulletin_id",
        [
            "FR-02-2026-02-13-20260213074407",
            "FR-01-2026-05-18-20260517140200",
            "FR-64-2026-05-18-20260517135700",
        ],
    )
    def test_matches_new_grammar(self, bulletin_id: str) -> None:
        """An id carrying a publication stamp matches.

        Args:
            bulletin_id: An id on the new grammar.

        """
        assert BULLETIN_ID_RE.match(bulletin_id)

    @pytest.mark.parametrize(
        "bulletin_id",
        [
            "FR-02-2026-02-13",
            "FR-2-2026-02-13-20260213074407",
            "FR-02-2026-02-13-2026021307440",
            "CH-2133-2026-02-13-20260213074407",
            "",
        ],
    )
    def test_rejects_other_forms(self, bulletin_id: str) -> None:
        """Old-grammar and non-MF ids do not match, so re-keying can find them.

        Args:
            bulletin_id: An id that is not on the new grammar.

        """
        assert BULLETIN_ID_RE.match(bulletin_id) is None
