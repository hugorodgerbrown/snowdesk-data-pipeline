"""
tests/bin/test_fetch_geoip_data.py — the GeoLite2 download's staging opt-out.

``bin/fetch-geoip-data`` runs on every web deploy. MaxMind's free tier allows
30 downloads per account per 24 hours, and staging redeploys on every merge to
``main``, so ``render.yaml`` sets ``GEOIP_DOWNLOAD=off`` on staging to stop its
deploys spending production's quota. These tests cover that contract:

* every supported opt-out spelling exits 0 **without calling curl**;
* an unset or enabled value still reaches the download.

The script is driven by subprocess with a stub ``curl`` first on ``PATH``. The
stub records each call and answers HTTP 429, which the script never retries
and treats as "fall back", so no test touches MaxMind or the installed
database.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "fetch-geoip-data"

# Answers like curl --write-out "%{http_code}" for a rate-limited request.
STUB_CURL = """#!/usr/bin/env bash
echo "$@" >> "${CURL_CALLS}"
printf '429'
"""


def _run(tmp_path: Path, geoip_download: str | None) -> tuple[int, list[str]]:
    """Run the script with a recording curl stub; return (exit code, curl calls)."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    curl = stub_dir / "curl"
    curl.write_text(STUB_CURL)
    curl.chmod(0o755)
    calls = tmp_path / "curl-calls"
    calls.touch()

    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "CURL_CALLS": str(calls),
        "MAXMIND_ACCOUNT_ID": "123",
        "MAXMIND_LICENSE_KEY": "test-key",
    }
    if geoip_download is not None:
        env["GEOIP_DOWNLOAD"] = geoip_download

    result = subprocess.run(  # noqa: S603 - absolute script path, test-owned env
        ["bash", str(SCRIPT)],  # noqa: S607 - bash resolved from the test PATH
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, calls.read_text().splitlines()


@pytest.mark.parametrize("value", ["off", "OFF", "Off", "false", "0", "no"])
def test_opt_out_skips_the_download(tmp_path: Path, value: str) -> None:
    """Each opt-out spelling exits 0 and never calls curl."""
    code, calls = _run(tmp_path, value)

    assert code == 0
    assert calls == []


@pytest.mark.parametrize("value", [None, "on", "true", "1", ""])
def test_unset_or_enabled_still_downloads(tmp_path: Path, value: str | None) -> None:
    """An unset or enabled value reaches curl once (a 429 is never retried)."""
    code, calls = _run(tmp_path, value)

    assert code == 0
    assert len(calls) == 1
    assert "download.maxmind.com" in calls[0]
