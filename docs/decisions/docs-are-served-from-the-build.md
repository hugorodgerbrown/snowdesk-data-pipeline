---
name: docs-are-served-from-the-build
description: MCP search_docs / read_doc / list_docs read public_corpus / ops_corpus from the deployed files, BM25 in process, split by audience
status: current
last-reviewed: 2026-11-02
---

# Docs are served from the build

## Decision

**The MCP servers answer "how does Snowdesk work?" from the docs the
running build ships, split by audience, ranked in process.** SNOW-1393.

* The public server (`/api/mcp/`) reads `public_corpus()`: the `/help/`
  topics, the bulletin guide and the explainers — what a rider is already
  shown. The staff ops server reads `ops_corpus()`: that, plus every
  living doc, decision record, runbook and `CLAUDE.md`.
* Both are built from files on disk once per process. Nothing is copied
  into a table, an index file or a vector store.
* Search is BM25 over headed sections in pure Python. There are no
  embeddings.

## Why

* **The build is the version.** A doc read from the deployed checkout
  describes the code beside it. A copy (a table, a hosted index) would need
  a sync step, and the first missed sync would answer with yesterday's app.
* **The split follows who wrote for whom.** The builder docs name file
  paths, runbooks and internal tickets. They are not secret (invariant 5
  keeps secrets out of the repo), but a rider asking how offline downloads
  work should get the help topic, not `_carryShellContent`. The help page
  already holds the line on gated features, and the corpus follows it: a
  flag-gated topic is in no public answer.
* **The corpus is small.** About 2.6 MB of Markdown in 240 docs. BM25
  answers in milliseconds, and the model calling the tool does the semantic
  work. Embeddings would add a model dependency, a build step and a cost
  per deploy, to rank a list the model then reads anyway.

## Consequences

* A new doc under `docs/`, a new decision or a new help topic is
  searchable on the next deploy, with no edit to the corpus code. The help
  topics are read from `public/help.html` itself.
* A doc must never hold a secret. That was already true, and it now
  matters at runtime as well.
* The public corpus can only grow by naming a source in
  `docs_corpus._public_docs`. Putting builder docs in front of riders is a
  deliberate edit, never a side effect of adding a file.
* The first docs call on a fresh process pays for building the corpus
  (about two seconds for the public one, which renders templates).
