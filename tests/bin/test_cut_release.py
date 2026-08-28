"""
tests/bin/test_cut_release.py — the release-number guard in ``bin/cut-release``.

``VERSION`` holds the release ordinal the account menu shows as "v24". It is
the one part of a release nothing else in the pipeline can compute: Render's
build gets ``RENDER_GIT_COMMIT`` and no tags, and ``release.yml`` creates the
CalVer tag only after the deploy has already started. So it is bumped by
hand, and a forgotten bump has no other symptom — the deploy is green, the
tests pass, and every user reading the menu is told they are on the previous
release for as long as nobody notices.

``bin/cut-release`` is the one point every production release passes
through, so the check lives there. These tests drive the real script against
a throwaway repository rather than parsing it, because what matters is the
exit code: a guard that prints a warning and returns zero would let the
release through and is indistinguishable from no guard at all.

The script is exercised in dry-run mode. The guard runs before the
``--commit`` branch, so no push is ever attempted and the test needs no
writable remote beyond the local bare repo it makes for itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "cut-release"

# Resolved to absolute paths once. Both are required for the test to mean
# anything, so a missing one should fail loudly here rather than as an
# obscure FileNotFoundError inside a fixture.
GIT = shutil.which("git") or "/usr/bin/git"
BASH = shutil.which("bash") or "/bin/bash"

# Identity and signing settings passed per-invocation rather than written
# into the throwaway repo's config: the machine running the suite may have
# `commit.gpgsign` on globally (this project signs every commit), and a
# fixture that tried to sign would hang waiting for a key.
GIT_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)


def _exec(
    args: list[str], cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess and return the completed process.

    Every process this module starts goes through here so the security
    suppression is stated once. Both executables are absolute paths
    resolved above, and every argument is either a literal or a path this
    test itself created — there is no external input anywhere in it.

    Args:
        args: Full argv, executable first.
        cwd: Working directory.
        check: Whether a non-zero exit should raise.

    Returns:
        The completed process, with stdout and stderr captured as text.

    """
    return subprocess.run(  # noqa: S603 - absolute executables, no external input
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def _git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` and return its stdout.

    Args:
        repo: Working directory for the command.
        *args: Arguments after ``git``.

    Returns:
        Captured stdout, stripped.

    """
    return _exec([GIT, *GIT_IDENTITY, *args], cwd=repo).stdout.strip()


def _bare_origin(tmp_path: Path) -> Path:
    """Create an empty bare repository to stand in for ``origin``.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Path to the bare repo, with ``main`` as its initial branch.

    """
    origin = tmp_path / "origin.git"
    _exec([GIT, "init", "--bare", "-b", "main", str(origin)], cwd=tmp_path)
    return origin


def _commit_version(repo: Path, value: str, subject: str) -> None:
    """Write ``VERSION`` and commit it.

    Args:
        repo: The working clone.
        value: Contents for the VERSION file.
        subject: Commit subject.

    """
    (repo / "VERSION").write_text(f"{value}\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", subject)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Return a clone whose ``origin`` has ``main`` and ``release`` at v23.

    The shape every real release starts from: ``release`` sits on the
    previously shipped commit, ``main`` has moved on.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Path to the working clone, with ``origin`` pointing at a bare repo.

    """
    origin = _bare_origin(tmp_path)

    work = tmp_path / "work"
    _exec([GIT, "clone", str(origin), str(work)], cwd=tmp_path)
    _commit_version(work, "23", "SNOW-1: release 23")
    _git(work, "push", "origin", "main")
    _git(work, "push", "origin", "main:refs/heads/release")
    return work


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the real ``bin/cut-release`` in dry-run mode inside ``repo``.

    Args:
        repo: The working clone to run in.

    Returns:
        The completed process, un-raised so the exit code can be asserted.

    """
    return _exec([BASH, str(SCRIPT)], cwd=repo, check=False)


def test_refuses_when_the_release_number_has_not_moved(repo: Path) -> None:
    """A release shipping the previous release's number is blocked.

    This is the whole point of the guard: the deploy would be perfectly
    healthy and the menu would still be wrong.
    """
    (repo / "shipped.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "shipped.txt")
    _git(repo, "commit", "-m", "SNOW-2: ship something, forget the bump")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 1, result.stdout
    assert "VERSION is still 23" in result.stderr


def test_names_the_bump_it_wants(repo: Path) -> None:
    """The refusal says what to do, not just that something is wrong.

    A guard that blocks a release at the moment someone is trying to ship
    owes them the next command.
    """
    _git(repo, "commit", "--allow-empty", "-m", "SNOW-3: no bump")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "> VERSION" in result.stderr
    assert "24" in result.stderr


def test_allows_a_release_whose_number_moved(repo: Path) -> None:
    """The ordinary case still goes through, and reports the transition."""
    _commit_version(repo, "24", "SNOW-4: bump to 24")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert "Release number: v23 → v24" in result.stdout
    assert "DRY RUN" in result.stdout


def test_does_not_block_a_release_predating_the_version_file(
    tmp_path: Path,
) -> None:
    """A ref with no ``VERSION`` at all is "cannot compare", not "stale".

    Every release cut before this file existed is in that position. The
    guard exists to catch a forgotten bump, and refusing to release
    against history it cannot read would be a different, unhelpful rule.
    """
    origin = _bare_origin(tmp_path)
    work = tmp_path / "work"
    _exec([GIT, "clone", str(origin), str(work)], cwd=tmp_path)
    (work / "readme.txt").write_text("no VERSION here\n", encoding="utf-8")
    _git(work, "add", "readme.txt")
    _git(work, "commit", "-m", "SNOW-5: before VERSION existed")
    _git(work, "push", "origin", "main")
    _git(work, "push", "origin", "main:refs/heads/release")
    _commit_version(work, "24", "SNOW-6: introduce VERSION")
    _git(work, "push", "origin", "main")

    result = _run(work)

    assert result.returncode == 0, result.stderr
