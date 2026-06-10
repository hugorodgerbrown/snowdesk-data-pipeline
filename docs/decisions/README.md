---
name: README
description: Decision log format — one accepted architectural decision per file, with context and consequences
status: current
last-reviewed: 2026-06-10
---

# Decision log

One file per accepted decision: the constraint, why it was chosen, and what
it rules out. These exist so that "why is it like this?" has a written answer
— the *what* lives in code; only the *why* belongs here.

Add a file when a decision (a) constrains future changes, (b) would look
arbitrary to someone who wasn't in the conversation, and (c) isn't derivable
from the code. Keep each file under a screenful. If a decision is reversed,
set `status: historical` and link the superseding file — don't delete it.

Format: frontmatter (`name`, `description`, `status`, `last-reviewed`), then
**Decision**, **Why**, and **Consequences** sections.
