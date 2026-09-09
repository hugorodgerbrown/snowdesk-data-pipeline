"""
tests/bin/test_cut_release.py — the release PR opened by ``bin/cut-release``.

A release is one pull request: a single commit bumping ``VERSION``, with the
release note in the description. Merging it is the release —
``release-sync.yml`` sees ``VERSION`` change on ``main``, fast-forwards
``release``, and tags the commit. The script therefore has to get three
things right before anyone can review them, because merging is irreversible
in the way a deploy is:

* the **ordinal**, which nothing else in the pipeline can compute (Render's
  build gets ``RENDER_GIT_COMMIT`` and no tags, and ``release.yml`` tags only
  after the deploy has started) — it is derived here rather than remembered;
* the **preconditions**, since a release opened while ``release`` has
  diverged, or while an earlier release is still syncing, would either be
  rejected on push or skip an ordinal;
* the **release note**, which is the only summary of what is shipping that a
  human reads before clicking merge.

These tests drive the real script against a throwaway repository rather than
parsing it, because what matters is the exit code: a check that prints a
warning and returns zero would let the release through and is
indistinguishable from no check at all.

The script is exercised in dry-run mode throughout. Every precondition runs
before the ``--commit`` branch, so no push is attempted and the tests need no
writable remote beyond the local bare repo they make for themselves.
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


def test_derives_the_next_release_number(repo: Path) -> None:
    """The ordinal is computed from ``release``, not asked for.

    The old flow bumped ``VERSION`` by hand in a separate PR and refused
    the release when it had not moved. Deriving it removes the failure
    the guard existed to catch.
    """
    _git(repo, "commit", "--allow-empty", "-m", "SNOW-2: ship something")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert "Release:      v23 → v24" in result.stdout
    assert "Branch:       release-v24" in result.stdout
    assert "DRY RUN" in result.stdout


def test_release_branch_is_not_a_path_under_release(repo: Path) -> None:
    """The branch is ``release-vNN``, which git can actually create.

    ``refs/heads/release`` and ``refs/heads/release/v24`` cannot coexist,
    so the slashed form would fail at push time — after the commit had
    been built, and only on a real release.
    """
    _git(repo, "commit", "--allow-empty", "-m", "SNOW-3: ship something")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "release-v24" in result.stdout
    assert "release/v24" not in result.stdout


def test_reports_nothing_to_release_when_the_refs_match(repo: Path) -> None:
    """Production is already on ``main`` — that is success, not an error."""
    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert "nothing to release" in result.stdout


def test_refuses_while_an_earlier_release_is_still_syncing(repo: Path) -> None:
    """A bump on ``main`` that ``release`` has not taken blocks a new release.

    That state means a release PR merged and ``release-sync.yml`` has not
    finished. Opening a second one would skip an ordinal and race the
    first workflow to advance ``release``.
    """
    _commit_version(repo, "24", "Bump VERSION to 24")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 1, result.stdout
    assert "already in flight" in result.stderr


def test_refuses_when_release_has_diverged_from_main(repo: Path) -> None:
    """A non-fast-forward advance is refused rather than forced.

    ``release`` carrying a commit that is not on ``main`` means someone
    advanced it out of band; the sync workflow's ref update would be
    rejected, so say so now instead of at merge time.
    """
    _git(repo, "checkout", "-B", "diverged", "origin/release")
    _git(repo, "commit", "--allow-empty", "-m", "out of band")
    _git(repo, "push", "--force", "origin", "diverged:refs/heads/release")
    _git(repo, "checkout", "main")
    _git(repo, "commit", "--allow-empty", "-m", "SNOW-4: ship something")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 1, result.stdout
    assert "not an ancestor" in result.stderr


def test_refuses_when_the_release_branch_already_exists(repo: Path) -> None:
    """An open release PR is not silently replaced."""
    _git(repo, "commit", "--allow-empty", "-m", "SNOW-5: ship something")
    _git(repo, "push", "origin", "main")
    _git(repo, "push", "origin", "main:refs/heads/release-v24")

    result = _run(repo)

    assert result.returncode == 1, result.stdout
    assert "already exists" in result.stderr


def test_refuses_when_the_release_ordinal_is_unreadable(repo: Path) -> None:
    """A ``VERSION`` that is not a number stops the release loudly.

    Guessing an ordinal from an unreadable one would put a wrong number
    in front of every user in the account menu.
    """
    # `release` must stay an ancestor of `main`, or the fast-forward check
    # fires first and this test would pass for the wrong reason.
    _commit_version(repo, "not-a-number", "break VERSION")
    _git(repo, "push", "origin", "main")
    _git(repo, "push", "--force", "origin", "main:refs/heads/release")
    _git(repo, "commit", "--allow-empty", "-m", "SNOW-6: ship something")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 1, result.stdout
    assert "does not hold a release ordinal" in result.stderr


def test_lists_each_ticket_under_its_own_subject(repo: Path) -> None:
    """The note gives one line per ticket, from that ticket's own commit.

    Matching a ticket anywhere in a subject picks up a merge commit that
    names three of them — repeating one line under each — and a
    ``Merge pull request #768 from …/fix/SNOW-781-…`` whose subject says
    nothing about what shipped. Both happened on the v28 range.
    """
    for subject in (
        "SNOW-778: capture and render hourly wind direction",
        "SNOW-779: show daily wind on the weather panel",
        "Merge pull request #767: wind everywhere (SNOW-778, SNOW-779)",
        "Merge pull request #768 from hugorodgerbrown/fix/SNOW-781-icon",
        "SNOW-781: stop the library asking for an icon that does not exist",
    ):
        _git(repo, "commit", "--allow-empty", "-m", subject)
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert "- SNOW-778: capture and render hourly wind direction" in result.stdout
    assert "- SNOW-779: show daily wind on the weather panel" in result.stdout
    assert (
        "- SNOW-781: stop the library asking for an icon that does not exist"
        in result.stdout
    )
    assert "Merge pull request" not in result.stdout


# ---------------------------------------------------------------------------
# SNOW-818: the commands an operator must run once production is up
# ---------------------------------------------------------------------------
#
# The script reads commit SUBJECTS, so a post-deploy step had no way to
# travel from the ticket that introduced it to the person merging the
# release. A `Deploy-Step:` trailer survives a squash-merge, so it reaches
# the range this script already reads.


def _commit_with_body(repo: Path, name: str, subject: str, body: str) -> None:
    """Add a file and commit it with a multi-line message.

    Args:
        repo: The working clone.
        name: Path of the file to create, relative to the repo.
        subject: Commit subject.
        body: Commit body, appended after a blank line.

    """
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(repo, "add", str(name))
    _git(repo, "commit", "-m", subject, "-m", body)


def test_collects_a_deploy_step_trailer_into_the_body(repo: Path) -> None:
    """A `Deploy-Step:` trailer reaches the release PR body.

    The whole point: the operator merging the release reads what they have
    to run, instead of remembering it.
    """
    _commit_with_body(
        repo,
        "apps/regions/management/commands/backfill_slugs.py",
        "SNOW-2: backfill resort slugs",
        "Deploy-Step: backfill_resort_slugs --commit",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "## Run after this deploys" in result.stdout
    assert "backfill_resort_slugs --commit" in result.stdout


def test_omits_the_section_when_there_is_no_step(repo: Path) -> None:
    """A release with no manual step carries no empty heading.

    An always-present section invites the operator to hunt for a step that
    is not there, which is how a real one stops being noticed.
    """
    _commit_with_body(repo, "apps/x.py", "SNOW-3: something ordinary", "No trailer.")
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "## Run after this deploys" not in result.stdout


def test_deduplicates_a_step_named_by_two_commits(repo: Path) -> None:
    """Two commits naming the same step produce one line, not two."""
    for n in (2, 3):
        _commit_with_body(
            repo,
            f"apps/regions/f{n}.py",
            f"SNOW-{n}: part {n}",
            "Deploy-Step: backfill_resort_slugs --commit",
        )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.stdout.count("- `backfill_resort_slugs --commit`") == 1


def test_warns_about_a_new_backfill_with_no_trailer(repo: Path) -> None:
    """The backstop, for the trailer nobody remembered to write.

    A warning on stderr and never a refusal — a backfill can legitimately
    ship without needing a production run, so blocking the release on a
    guess would be worse than reporting what was noticed.
    """
    _commit_with_body(
        repo,
        "apps/regions/management/commands/backfill_forgotten.py",
        "SNOW-4: add a backfill and forget the trailer",
        "No trailer here.",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 0, "the backstop warns, it must never refuse"
    assert "backfill_forgotten" in result.stderr
    assert "no Deploy-Step" in result.stderr


def test_does_not_warn_when_the_backfill_is_already_named(repo: Path) -> None:
    """A trailer naming the command silences the backstop for it."""
    _commit_with_body(
        repo,
        "apps/regions/management/commands/backfill_named.py",
        "SNOW-5: add a backfill and say so",
        "Deploy-Step: backfill_named --commit",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "backfill_named" not in result.stderr


def test_does_not_warn_about_a_non_backfill_command(repo: Path) -> None:
    """Only the one-shot data commands are the backstop's business.

    Every other new command is ordinary code that ships and runs itself.
    """
    _commit_with_body(
        repo,
        "apps/regions/management/commands/fetch_something.py",
        "SNOW-6: an ordinary command",
        "No trailer.",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "fetch_something" not in result.stderr


def test_warns_about_a_new_fill_command_with_no_trailer(repo: Path) -> None:
    """SNOW-889: `fill_*` is a one-shot data command too.

    v33 shipped `fill_location_elevations` and `fill_what3words`, both of
    which production had to be walked with, and the backstop said nothing
    because it matched `backfill_` alone.
    """
    _commit_with_body(
        repo,
        "apps/locations/management/commands/fill_what3words.py",
        "SNOW-7: fill the estate's three word addresses",
        "No trailer.",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert "fill_what3words" in result.stderr


def test_does_not_warn_when_the_fill_command_is_named(repo: Path) -> None:
    """The trailer suppresses the warning for every prefix, not just one."""
    _commit_with_body(
        repo,
        "apps/locations/management/commands/fill_named.py",
        "SNOW-8: fill something and say so",
        "Deploy-Step: fill_named --commit",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "fill_named" not in result.stderr


def test_warns_about_a_new_link_command_with_no_trailer(repo: Path) -> None:
    """`link_*` completes the family: the region-centroid links are one."""
    _commit_with_body(
        repo,
        "apps/regions/management/commands/link_resort_locations.py",
        "SNOW-9: link resorts to their locations",
        "No trailer.",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert "link_resort_locations" in result.stderr


def test_lists_a_two_ticket_subject_once(repo: Path) -> None:
    """SNOW-890: a subject naming two tickets is one line, not two.

    Neither id matches the strict `^SNOW-nnn[[:punct:]]` lookup — the first
    is followed by a space, the second does not open the subject — so both
    fall through to the loose match and resolve to the same subject. v33's
    PR body carried `SNOW-847 / SNOW-692: …` twice.
    """
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "SNOW-847 / SNOW-692: pin a download's glyphs and slope tiles",
    )
    _git(repo, "push", "origin", "main")

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.count(
            "- SNOW-847 / SNOW-692: pin a download's glyphs and slope tiles"
        )
        == 1
    )
