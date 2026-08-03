"""
tests/core/test_settings_spec.py — Tests for the environment-settings spec
(SNOW-580): the shape validators, the redacted report, and the guard that
keeps the spec in step with ``config/settings/``.

The incident this came from was a bare
``OPEN_METEO_API_BASE_URL=customer-api.open-meteo.com`` accepted at startup
and only failing part-way through a fetch batch, so the cases that matter
most are the "looks plausible but is unusable" ones — a host with no scheme,
and a ``file://`` URI whose first path segment is silently parsed as a host.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import override_settings

from apps.core import settings_spec
from apps.core.settings_spec import (
    REDACTED,
    SETTINGS_SPEC,
    UNSET,
    absolute_url,
    display_value,
    iter_settings_report,
    local_mirror_url,
    non_empty,
    positive_int,
    positive_number,
    spec_by_name,
)

SETTINGS_DIR = Path(settings_spec.__file__).resolve().parents[2] / "config" / "settings"


class TestAbsoluteUrl:
    """Tests for the http(s) URL validator."""

    @pytest.mark.parametrize(
        "value",
        [
            "https://api.open-meteo.com/v1",
            "http://localhost:8000",
            "https://snowdesk.info",
            "",
            None,
        ],
    )
    def test_accepts_valid_values(self, value: object) -> None:
        """Absolute http(s) URLs and empty values pass."""
        assert absolute_url(value) is None

    def test_rejects_a_bare_host(self) -> None:
        """The exact incident: a host with no scheme."""
        problem = absolute_url("customer-api.open-meteo.com")

        assert problem is not None
        assert "no scheme" in problem

    @pytest.mark.parametrize(
        "value", ["ftp://example.com", "file:///tmp/x", "gopher://example.com"]
    )
    def test_rejects_non_http_schemes(self, value: str) -> None:
        """Only http and https reach these APIs."""
        assert absolute_url(value) is not None

    def test_rejects_a_non_string(self) -> None:
        """A mis-cast setting is reported rather than crashing the check."""
        assert absolute_url(123) is not None


class TestLocalMirrorUrl:
    """Tests for the dev-mirror validator, which also accepts file:// URIs."""

    @pytest.mark.parametrize(
        "value",
        [
            "http://localhost:8000/dev/slf-mirror",
            "file:///Users/someone/mirrors/meteofrance",
            "file:////Users/someone/mirrors/meteofrance",
            "",
        ],
    )
    def test_accepts_valid_values(self, value: str) -> None:
        """http(s) mirrors and absolute file:// URIs pass."""
        assert local_mirror_url(value) is None

    def test_rejects_a_relative_file_uri(self) -> None:
        """``file://relative/path`` parses its first segment as the host.

        ``config/settings/development.py`` warns about this in a comment;
        nothing enforced it until SNOW-580. The directory actually read is
        not the one written, and the fetcher fails with no useful message.
        """
        problem = local_mirror_url("file://docs/research/meteofrance")

        assert problem is not None
        assert "host part" in problem
        assert "docs" in problem

    def test_still_rejects_a_bare_host(self) -> None:
        """Non-file values fall through to the http(s) rules."""
        assert local_mirror_url("localhost:8000/dev") is not None


class TestNumericValidators:
    """Tests for the positive-number validators."""

    @pytest.mark.parametrize("value", [1, 25, 86400])
    def test_positive_int_accepts_positives(self, value: int) -> None:
        """Positive integers pass."""
        assert positive_int(value) is None

    @pytest.mark.parametrize("value", [0, -1, "25", 1.5, True])
    def test_positive_int_rejects_everything_else(self, value: object) -> None:
        """Zero, negatives, strings, floats and bools are all rejected.

        ``True`` matters: ``isinstance(True, int)`` is ``True`` in Python, so
        a bool would otherwise pass as a valid positive integer.
        """
        assert positive_int(value) is not None

    @pytest.mark.parametrize("value", [0.5, 10.0, 3])
    def test_positive_number_accepts_positives(self, value: object) -> None:
        """Positive ints and floats pass."""
        assert positive_number(value) is None

    @pytest.mark.parametrize("value", [0, -0.5, "10", True])
    def test_positive_number_rejects_everything_else(self, value: object) -> None:
        """Zero, negatives, strings and bools are rejected."""
        assert positive_number(value) is not None


class TestNonEmpty:
    """Tests for the emptiness validator."""

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_rejects_empty_values(self, value: object) -> None:
        """Empty, whitespace-only and None all fail."""
        assert non_empty(value) is not None

    def test_accepts_a_real_value(self) -> None:
        """A non-blank string passes."""
        assert non_empty("x") is None


class TestDisplayValue:
    """Tests for the redaction applied to the dump."""

    def test_redacts_a_secret(self) -> None:
        """A secret's value never appears, not even a prefix."""
        spec = spec_by_name()["SECRET_KEY"]

        shown = display_value(spec, "super-secret-value")

        assert shown == REDACTED
        assert "super" not in shown

    def test_shows_a_non_secret(self) -> None:
        """A non-secret is shown verbatim."""
        spec = spec_by_name()["SITE_BASE_URL"]

        assert display_value(spec, "https://snowdesk.info") == "https://snowdesk.info"

    @pytest.mark.parametrize("value", ["", None, []])
    def test_marks_empty_values_as_unset(self, value: object) -> None:
        """Empty renders as an explicit marker rather than a blank column."""
        spec = spec_by_name()["OPEN_METEO_API_KEY"]

        assert display_value(spec, value) == UNSET

    def test_redacts_an_empty_secret_as_unset_not_redacted(self) -> None:
        """An unset secret reads as unset — "redacted" would imply it is set."""
        spec = spec_by_name()["METEOFRANCE_API_KEY"]

        assert display_value(spec, "") == UNSET


class TestSettingsReport:
    """Tests for the report backing manage.py dump_settings."""

    def test_covers_every_spec_entry(self) -> None:
        """One row per spec entry, in spec order."""
        rows = list(iter_settings_report())

        assert [row.name for row in rows] == [spec.name for spec in SETTINGS_SPEC]

    @override_settings(OPEN_METEO_API_BASE_URL="customer-api.open-meteo.com")
    def test_reports_a_validation_problem(self) -> None:
        """A bad value carries its problem, so the dump doubles as a diagnosis."""
        row = next(
            r for r in iter_settings_report() if r.name == "OPEN_METEO_API_BASE_URL"
        )

        assert row.problem is not None
        assert "no scheme" in row.problem

    def test_never_leaks_a_secret_value(self) -> None:
        """No secret's real value appears anywhere in the report."""
        from django.conf import settings as dj_settings

        for row in iter_settings_report():
            spec = spec_by_name()[row.name]
            if not spec.secret:
                continue
            real = getattr(dj_settings, row.name, None)
            if real:
                assert str(real) not in row.value


class TestSpecCoverage:
    """Guards the spec against drifting behind config/settings/."""

    def test_covers_every_config_call_in_settings(self) -> None:
        """Every ``NAME = config(...)`` in config/settings/ has a spec entry.

        Without this, a setting added later silently gets no validation and
        never appears in the dump — which is the gap SNOW-580 exists to
        close, reopened one setting at a time.
        """
        pattern = re.compile(r"^([A-Z_][A-Z0-9_]*) *= *config\(", re.MULTILINE)
        found: set[str] = set()
        for path in sorted(SETTINGS_DIR.glob("*.py")):
            found |= set(pattern.findall(path.read_text(encoding="utf-8")))

        missing = sorted(found - set(spec_by_name()))

        assert not missing, (
            "These settings are read from the environment but have no "
            f"SETTINGS_SPEC entry: {missing}. Add one to "
            "apps/core/settings_spec.py (validator=None is fine when the "
            "value has no validatable shape)."
        )

    def test_has_no_entries_for_settings_that_no_longer_exist(self) -> None:
        """Every spec entry resolves against django.conf.settings."""
        from django.conf import settings as dj_settings

        unresolved = [
            spec.name for spec in SETTINGS_SPEC if not hasattr(dj_settings, spec.name)
        ]

        assert not unresolved, f"SETTINGS_SPEC names that no longer exist: {unresolved}"

    def test_secrets_are_marked(self) -> None:
        """Anything named like a credential is flagged secret.

        A missed flag prints the value in the dump, which is the one
        outcome this command must never produce.
        """
        credential_like = re.compile(r"(KEY|PASSWORD|SECRET|TOKEN)$")
        unmarked = [
            spec.name
            for spec in SETTINGS_SPEC
            if credential_like.search(spec.name)
            and not spec.secret
            # Public-by-design values that merely end in a credential-ish word.
            and spec.name not in {"ACCOUNT_TOKEN_MAX_AGE"}
        ]

        assert not unmarked, f"Credential-shaped settings not marked secret: {unmarked}"
