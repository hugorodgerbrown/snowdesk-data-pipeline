# /snow-pr — Open a PR for the current Snowdesk branch

Gate: run the QA agent first. Only open a PR if QA reports the branch ready.

## Steps

1. **Identify the ticket** — read the current branch name (e.g. `feature/SNOW-42-…`).
   Extract the ticket identifier (`SNOW-42`). If the branch does not follow the
   `feature|fix|chore/SNOW-xxx-…` naming convention, stop and tell the user.

2. **Fetch the Linear ticket** — use the Linear MCP to retrieve the ticket title
   and confirm it is in `In Progress`. If it is still at `Ready for dev`, that
   means the move-to-`In Progress` step was skipped at branch creation; warn
   the user and move it to `In Progress` via MCP before proceeding. Any other
   status (`Done`, `Canceled`, `Backlog`, etc.) is unexpected — stop and ask.

3. **Run the QA agent** — invoke the `qa` subagent against the current working
   tree. Ask it to cover all features touched by this branch (read the diff from
   `git diff main...HEAD` to scope its focus).

4. **Gate on QA verdict**:
   - If the QA output contains `READY FOR PR` → proceed to step 5.
   - If it does not → print the full QA report, tell the user what needs fixing,
     and **stop here**. Do not open a PR.

5. **Build the PR body** using this template:

   ```
   Closes SNOW-xxx

   ## Summary
   <1–3 bullet points derived from the ticket and diff>

   ## Test plan
   <bulleted checklist taken from the QA agent's scenario titles>

   🤖 Generated with [Claude Code](https://claude.ai/code)
   ```

6. **Open the PR** with `gh pr create`:
   - Title: `SNOW-xxx: <ticket title in imperative form>`
   - Body: the template above (pass via HEREDOC to preserve formatting)
   - Base branch: `main`

7. **Report back** with the PR URL and confirm the Linear ticket is moving to
   `In Review` (GitHub integration handles this automatically on PR open).

## Invariants to check during QA

The QA agent must confirm — at minimum — that none of the
[`## Invariants`](../CLAUDE.md) in CLAUDE.md have been violated by changes
on this branch.
