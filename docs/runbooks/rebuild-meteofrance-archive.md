---
name: rebuild-meteofrance-archive
description: Regenerate meteofrance_archive.ndjson from the local BRA PDFs — where they live, how to parallelise, what to assert before committing
status: current
last-reviewed: 2026-07-30
---

# Rebuild the Météo-France archive

`bulletins/local_mirrors/meteofrance_archive.ndjson` is a **generated artefact**.
When the extractor in `scripts/meteofrance-archive/` changes, the committed
NDJSON has to be regenerated or the fix reaches no data.

The rebuild is offline. Nothing here runs in CI, and the only thing that lands in
a PR is the regenerated NDJSON.

## Where the source PDFs are

`DO_NOT_ADD/meteofrance-archive/bra_pdfs` — 4,671 PDFs, 5.3 GB, in the **primary
worktree** (not in a Claude worktree). Gitignored, and to stay that way.

The set includes 122 `.N`-suffixed files. Those are **not** an error: aria2c
renamed them when `build_aria2c_input.normalise_filename()` collapsed two issues
of one massif-day onto one output filename. They are genuine separate downloads
— 90 distinct bulletins plus 32 byte-identical duplicates — so the rebuild needs
all of them.

If the PDFs are missing, they can be re-downloaded from the URLs in
`bra_working_files.zip → bra_urls.csv`; Météo-France's open-data endpoint still
served the full 2025-11 → 2026-05 range as of 2026-07-30, but it rotates files
eventually, so treat the local copy as the primary source.

## Rebuild

Serial, for a small input:

```bash
uv run python scripts/meteofrance-archive/mf_bra_to_caaml.py --input DO_NOT_ADD/meteofrance-archive/bra_pdfs --output /tmp/rebuilt.ndjson --no-resume
```

That runs at roughly 1.1 records/second — about 70 minutes for the full archive.
Splitting the input across workers cuts it to around 12 minutes on an 8-core
machine; symlink the PDFs into N chunk directories, run one process per chunk
with its own `--output`, then concatenate the results. Order does not matter:
the loader is order-independent by design.

`--no-resume` overwrites. Omit it to resume an interrupted run — the script skips
any PDF whose `source_file` already appears in the output.

## Assert before committing

The NDJSON is 4,671 lines and ~25 MB (up from 12 MB before the prose fix); it is
reviewed by these assertions, not by reading the diff.
`tests/bulletins/test_meteofrance_archive_integrity.py` runs all of them, so
`tox -e test` is the quickest check.

Sort the file before committing (`sort rebuilt.ndjson > …`) — parallel workers
emit interleaved, and a stable order keeps future rebuild diffs readable.

| Check | Expected |
|---|---|
| records | 4,671 |
| distinct `bulletinID` | **4,639** |
| records with no `publicationTime` | 0 |
| covered date more than 2 days after publication | 0 |
| any covered date after 2026-05-22 | none |

The gap between 4,671 records and 4,639 ids is the 32 byte-identical duplicate
downloads, which correctly coalesce on load. A count **below** 4,639 means two
distinct bulletins are sharing an id — the failure mode the identity scheme
exists to prevent, so investigate rather than proceed (see
[`meteofrance-bulletin-identity`](../decisions/meteofrance-bulletin-identity.md)).

Prose is harder to assert mechanically, because the PDF soft-wraps sentences
across visual lines — a line ending without punctuation is normal, so
"no line ends mid-word" is not a usable check. Compare against the previous
archive instead: snowpack and activity comments should roughly double in length
(measured 1.9×–2.4× across four bulletins). A rebuild where prose length is flat
means a crop bound has regressed.

## Then load it

```bash
uv run python manage.py rekey_meteofrance_bulletins --commit
```

Re-keys any rows still on the old `FR-{NN}-{covered date}` identifier. Idempotent
and read-only without `--commit`. Then load the rebuilt archive — via the Django
admin upload view or `scripts/meteofrance-archive/load_archive.py` — which
creates the issues that the old identifier had been overwriting.

Watch the load summary for `id-collision`: it should be `0`. `duplicate` should
be 32 for the full archive.

## Reproducibility

Extraction reads no clock and depends on no ambient state, so the same PDFs must
always yield the same records
([why](../decisions/meteofrance-extraction-is-deterministic.md)). Any diff
between two rebuilds that a code change does not explain is a bug.
