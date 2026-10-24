"""
tests/test_testmon_support.py — the two pytest-testmon fixes in tests/conftest.py.

SNOW-1221 runs a pull request's tests through pytest-testmon, and two
things in this repo broke it before a single test was selected:

* coverage's default ``sys.monitoring`` core on Python 3.14 records each
  line for the first test that runs it only, so the map under-records —
  ``_require_ctrace_under_testmon`` refuses such a run;
* testmon raised ``IndexError`` on the extensionless ``bin/`` scripts that
  ``tests/bin/`` imports — ``_let_testmon_read_extensionless_scripts``
  hands it a filename that reads as Python.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    _PythonFilename,
    _require_ctrace_under_testmon,
)


class _Config:
    """The one ``pytest.Config`` method the guard reads."""

    def __init__(self, **options: bool) -> None:
        """Hold the options this fake reports as set."""
        self.options = options

    def getoption(self, name: str, default: Any = None) -> Any:
        """Return a set option, or ``default``."""
        return self.options.get(name, default)


class TestRequireCtrace:
    """A ``--testmon`` run must measure with coverage's ctrace core."""

    def test_a_plain_run_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without testmon the core does not matter."""
        monkeypatch.delenv("COVERAGE_CORE", raising=False)
        _require_ctrace_under_testmon(_Config())  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "option", ["testmon", "testmon_noselect", "testmon_forceselect"]
    )
    def test_testmon_without_ctrace_is_refused(
        self, option: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every testmon mode that records a map needs ctrace."""
        monkeypatch.setenv("COVERAGE_CORE", "sysmon")
        with pytest.raises(pytest.UsageError, match="COVERAGE_CORE=ctrace"):
            _require_ctrace_under_testmon(_Config(**{option: True}))  # type: ignore[arg-type]

    def test_testmon_with_ctrace_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The CI configuration passes."""
        monkeypatch.setenv("COVERAGE_CORE", "ctrace")
        _require_ctrace_under_testmon(_Config(testmon=True))  # type: ignore[arg-type]


class TestPythonFilename:
    """The filename testmon is given for an extensionless script."""

    def test_testmon_reads_the_extension_as_py(self) -> None:
        """testmon's ``rsplit(".", 1)[1]`` gets ``py``."""
        assert _PythonFilename("bin/select-tests").rsplit(".", 1)[1] == "py"

    def test_every_other_use_is_the_plain_path(self) -> None:
        """Joining, hashing and other splits see the real name."""
        name = _PythonFilename("bin/select-tests")
        assert name == "bin/select-tests"
        table: dict[str, int] = {name: 1}
        assert table["bin/select-tests"] == 1
        assert name.rsplit("/") == ["bin", "select-tests"]

    def test_the_shim_is_installed(self) -> None:
        """tests/conftest.py's pytest_configure patched testmon's reader."""
        from testmon.testmon_core import SourceTree

        assert getattr(SourceTree.get_file, "snowdesk_shim", False)
