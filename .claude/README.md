# Snowdesk Claude Code workflow

Skills and agents for taking a Linear ticket from a one-line description
through to a merged PR, plus a few utilities (security audit, project status
updates).

## Layout

```
.claude/
├── skills/
│   ├── scope/                  ← Todo → Ready for dev
│   ├── implement/              ← Ready for dev → In Progress → PR open
│   ├── work-on/                ← scope + implement chained in one session
│   ├── merge-prs/              ← land a batch of PRs, one at a time
│   ├── release/                ← fast-forward `release` → production deploy
│   ├── audit-security/         ← security audit of the codebase
│   ├── audit-code/             ← whole-codebase drift audit (SNOW-269)
│   ├── post-project-update/    ← Linear project status update (used by Routine)
│   └── create-ticket/          ← make a Linear ticket (front of the lifecycle)
└── agents/
    ├── scoper.md               ← used by scope
    ├── implementer.md          ← used by implement
    ├── reviewer.md             ← used by implement
    ├── code-auditor.md         ← used by audit-code
    ├── security-auditor.md     ← used by audit-security
    └── documenter.md           ← on-demand doc maintenance
```

## The two layers

| Concept | What it is | How invoked | Where it lives |
|---|---|---|---|
| **Agent** | Subprocess with its own system prompt, tool allowlist, and model. Returns one message. | `Agent(subagent_type=…)` — usually by a skill | `.claude/agents/<name>.md` |
| **Skill** | A reusable playbook. Description carries trigger phrases; the body loads only when invoked. | Typed as `/<name>`, or auto-invoked when the user's phrasing matches the description | `.claude/skills/<name>/SKILL.md` |

(There used to be a third layer — `.claude/commands/` slash commands — but
commands and skills are now a unified mechanism, and the one command,
`snow-audit`, has been converted to the `audit-security` skill.)

## Naming convention

- **kebab-case, lowercase.**
- **No namespace prefixes** (`snow-`, `linear-`): project skills are already
  scoped to this project. Prefixes are for plugins.
- **Every skill is an imperative verb phrase** — the name completes
  "Claude, …" (`scope`, `implement`, `create-ticket`, `work-on`, `merge-prs`,
  `post-project-update`). Keep the name as short as unambiguity allows; add
  the object as soon as the bare verb could mean two things — which is why
  the security audit is `audit-security` and the drift audit is `audit-code`,
  not a bare `audit` that competes with both.

  There is **no separate "reference skill" category**, and no `-guide`
  suffix. There used to be two, and both turned out to be misfiled: one
  duplicated `implement`'s trigger and was deleted; the other described the
  procedure for making a ticket and is now simply `create-ticket`. If a skill
  earns its keep, it is because Claude *does* something with it — name the
  act. Genuine background knowledge that isn't an action belongs in
  `CLAUDE.md` or `docs/`, not in a skill with a noun for a name.
- **Agents are agent-nouns naming the role** (`scoper`, `implementer`,
  `reviewer`, `code-auditor`, `security-auditor`). Where an agent is the sole
  worker for a skill, the pair should read as verb → actor on the same
  object, so the mapping is guessable from either side:

  ```
  scope          → scoper
  implement      → implementer
  audit-code     → code-auditor
  audit-security → security-auditor
  ```

  Not every skill needs an agent — `create-ticket`, `merge-prs`, `release`
  and `post-project-update` do their work inline, and that is fine. An agent
  nothing invokes and nobody asks for is dead weight, though: delete it
  rather than rename it. (`qa` was removed on exactly those grounds: no
  caller, no use in 464 sessions.)

**One deliberate exception: `release` is a bare noun.** The imperative rule
exists to keep *auto-invocation* unambiguous, and `release` carries
`disable-model-invocation: true` — it can only be typed, so there is nothing
to disambiguate at the point of use. Don't "fix" it to `cut-release`.

## Frontmatter rules

Three fields carry weight; the rest is prose.

- **`allowed-tools` is a grant, not a restriction.** It pre-approves tools for
  the turn that invokes the skill — it never removes one. So a name that
  matches nothing fails *silently*: you get a permission prompt where you
  expected none, and nothing errors. Use the canonical tool names from the
  [tools reference](https://code.claude.com/docs/en/tools-reference) — in
  particular the subagent tool is **`Agent`**, not `Task`, and there is no
  `Task` alias. To actually withhold a tool, use `disallowed-tools`.
- **MCP grants name a configured server**: the Linear connector UUID
  `mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8`, not `mcp__linear`. Required on
  every skill, not just Routine ones — see "Linear MCP permissions" below.
- **`disable-model-invocation: true` on anything with an irreversible side
  effect** (`release` deploys production, `merge-prs` squash-merges). It makes
  the skill human-only. Do **not** put it on a skill a Routine may fire —
  `post-project-update` and `audit-code` both support unattended mode, and
  the flag also blocks a scheduled task.

`user-invocable` defaults to `true`; set it to `false` only for a reference
skill that isn't a meaningful thing to type.

## Ticket lifecycle

```
(conversation) ──"make a ticket"──▶ Backlog / Todo ──"scope NN"──▶ Ready for dev
                       │                                  │
                 (create-ticket:                    (scope skill +
                  may post a clean                   scoper agent)
                  scope and land                          │
                  straight in Ready)                      │
                                                          ▼
                            "implement NN" ──────▶ In Progress
                                    │
                            (implement skill:
                             branch + plan gate,
                             implementer ⇄ reviewer,
                             push + PR)
                                    ▼
              PR opened ──▶ In Review ──merge──▶ Done
                                  (GitHub–Linear integration)
```

`work-on NN` chains the two: scope (if the ticket is still in Todo) then
implement, in one session. Two approval gates either way: the scope and the
implementation plan.

## Auto-invocation phrases

You don't need to remember slash commands — say what you want:

- "make a ticket for that" / "log this as a bug" → create-ticket
- "scope SNOW-42" → scope
- "implement 42" / "go ahead and build it" → implement
- "work on 42" / "take 42 through to review" → work-on
- "run a security audit" → audit-security
- "audit the code for drift" → audit-code
- "post a daily update for Snowdesk" → post-project-update

A bare "audit the project" matches neither on purpose — say which audit you
mean, or Claude will ask.

`merge-prs` and `release` are the exception: they carry
`disable-model-invocation: true`, so no phrasing triggers them. Type
`/merge-prs` or `/release`.

## State machine guarantees

Each workflow skill enforces a hard precondition on Linear ticket state:

| Skill         | Required state         | Transitions to              |
|---------------|------------------------|-----------------------------|
| create-ticket | — (creates the ticket) | Backlog, Todo, or Ready for dev |
| scope         | Todo                   | Ready for dev               |
| implement     | Ready for dev          | In Progress, then In Review |
| work-on       | Todo or Ready for dev  | In Review                   |

`create-ticket` is the only one with no required starting state — it makes the
ticket. It lands in `Ready for dev` only when it can post a scoping comment
with no open questions; otherwise `Todo`, or `Backlog` if untriaged.

Tickets in the wrong state cause the skill to stop and explain why.

## post-project-update (special case)

`post-project-update` is the one skill that runs **unattended** — a scheduled
Routine invokes it once a day and posts the resulting status update to Linear
without an approval gate. The skill lives in this repo (not under `~/.claude/`)
so the remote Routine environment can find it after cloning.

When you invoke it interactively (e.g. "post a project update for Snowdesk"),
the approval gate is restored: draft → review → post.

## MCP permissions

Two connectors, both from claude.ai, both authorised against the Anthropic
account so they work in local, remote and Routine sessions alike:

- **Linear** — full read/write. Every skill here talks to Linear through it.
- **Render** — read-only. Services, deploys, logs, metrics and workspaces are
  pre-approved; the write tools are denied outright, and the Postgres and Key
  Value tools are denied too (`query_render_postgres` runs SQL against the
  production database, and the Key Value tools can surface secrets).

### The server name depends on the client surface

A permission rule matches the tool NAME, and **the prefix differs between
clients**. Three prefixes have each been observed actually invoking Linear:

| Prefix | `entrypoint` | Sessions observed |
|---|---|---|
| `mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8__` | `claude-desktop` | 75 |
| `mcp__claude_ai_Linear__` | `cli` | 1 (2026-09-04) |
| `mcp__Linear__` | cloud | 1 (2026-09-04 probe) |

**How much to trust this.** The Desktop row is solid. The other two rest on a
single observation each, because the local transcript history is almost
entirely Desktop (~101k desktop records against ~1.7k cli). No client has ever
been recorded using another client's prefix, so there is no counterexample —
but absence of one across a lopsided sample is weak evidence, not proof.

**The mechanism, settled 2026-09-05.** There is ONE Linear connector install.
`ListConnectors` reports it as `installedServerId:
bee16520-0a2b-446d-b267-fbf9f62cf3a8`, `installState: connected` — the same id
the Desktop prefix uses — so the three prefixes are one install rendered under
different names per client, not three installs. The transcripts could not
distinguish this and `~/.claude.json` does not carry the id; `ListConnectors`
does, and it is the cheapest way to check the id is still current after a
reconnect. The table still governs what to write: the rendering rule per client
remains unobserved, so list every prefix regardless.

Render is assumed to behave the same way: `mcp__f5febe82-…__` is observed on
`claude-desktop` (6 sessions), and `mcp__Render__` is #783's claim for cloud.
The CLI and cloud rows for Render are unobserved.

**Regardless of mechanism, list every prefix and remove none.** The argument is
cost asymmetry, not sample size: a prefix absent from this client is inert and
silent, while a missing one costs an approval prompt — or, unattended, a stall.
That asymmetry is what #783 got wrong on 2026-09-02, concluding from a tool
listing that the UUID "never appears in a tool name" and swapping it out.

**Confirm a name by invocation, never by reading a tool listing** — and when
you check the transcripts, the client field is `entrypoint`.

The failure is silent in both directions, which is what makes this so easy to
get wrong: a rule naming a server that is not present on this surface is inert
and raises no error, and the only symptom of a missing one is an approval
prompt where you expected none — or, in an unattended run, a stall.

### If you are seeing a Linear approval prompt, read this first

**Do not start by editing `settings.json`.** Four consecutive sessions
(2026-09-02 to 2026-09-05) "fixed" a recurring Linear prompt there and the
prompt came back, because Gate 2 was not what was firing. The repo side was
re-verified end to end on 2026-09-05 and is correct:

| Checked | Result |
|---|---|
| `permissions` block nesting in `settings.json` | correct (rules under `permissions`, not top level) |
| `mcp__<server>__*` allow syntax | valid — [docs](https://code.claude.com/docs/en/permissions#mcp) say a glob is accepted after a literal `mcp__<server>__` prefix |
| All three Linear prefixes present | yes (`mcp__Linear__*`, `mcp__claude_ai_Linear__*`, `mcp__bee16520-…__*`) |
| Connector install id still current | yes — `ListConnectors` returns `bee16520-…`, connected |
| Every Linear-touching skill's `allowed-tools` | all nine carry the three server names |
| Stale server names (`linear-server`, `mcp__linear__…`) | none anywhere in `.claude/` |

With all of that true, a prompt that still appears is **Gate 1**, below, which
no file in this repo can override. Go to claude.ai → Settings → Connectors →
Linear and look at the tool the prompt names; if it reads `Ask`, that is the
whole cause, and setting it to `Allow` is the whole fix.

**Confirmed 2026-09-05.** That is what it was: the prompts stopped after a
change at the connector, with no repo change involved. Four days of
`settings.json` edits could not have worked, and the next session should not
try a fifth.

Read the tool name off the prompt itself ("Allow Claude to use **Get issue**
(Linear)?") — it is the one piece of evidence that distinguishes the gates, and
it is not recoverable afterwards from anything in the repo.

### Two gates, and only one is in this repo

**Gate 1 — the connector's own per-tool permissions**, at claude.ai →
Settings → Connectors. A tool set to `ask` there prompts on every call and no
allow rule or permission mode overrides it. Check here first; it is usually
already `allow`.

The 2026-09-04 cloud probe demonstrated this gate directly: `get_issue` and
`save_comment` ran unprompted while `delete_comment` asked, in a session whose
`settings.json` allowed `mcp__Linear__*` wholesale. An allow rule cannot widen
past Gate 1. That also makes Gate 1 the **strongest** place to enforce Render
read-only — setting Render's write tools to `block` at claude.ai holds in every
environment, needs no name guessing, and survives an untrusted checkout.

**Gate 2 — Claude Code's permission system.** `permissions.allow` in
`.claude/settings.json` is discarded entirely in an untrusted folder, and a
remote Routine starts in a fresh checkout that has never seen a trust dialog.
**Workspace trust never gates a skill's `allowed-tools`**, so that frontmatter
is the grant that survives — which is why every Linear-touching skill carries
the names itself rather than relying on `settings.json`.

That failure mode is not hypothetical: on 2026-08-23 a Routine blocked on
"Allow Claude to use Get issue (Linear)?" because `scope`, `implement`,
`work-on` and `create-ticket` still named a deleted `linear-server` while the
other three skills had been migrated.

Diagnosis order when a session stalls on a connector prompt: the connector's
tool permission at claude.ai → the skill's `allowed-tools` → and only then
`settings.json`, which cannot help an untrusted run.

**Do not add a `.mcp.json`.** It would declare an OAuth-only server that a
remote container cannot authenticate (the flow needs an interactive session),
and project-scoped servers carry their own first-use trust prompt on top. A
local server also *shadows* the connector of the same name — that is what
`linear-server` and `render` did until SNOW-717, reporting "needs
authentication" forever while the connectors worked and stayed invisible.
Remote access already works via the connectors. An API key must never be
committed here, including through `${VAR}` interpolation — see invariant 5 in
[`CLAUDE.md`](../CLAUDE.md).

### Where the Bash grants live

`.claude/settings.local.json` is **gitignored**, so nothing in it reaches a
cloud session. Every grant the documented dev loop needs — `uv`, `npm`, `gh`,
`git`, `bin/*`, `pre-commit` — therefore lives in the committed
`settings.json`. Keep `settings.local.json` for genuinely machine-local things
only; a grant parked there is invisible to Cloud and to Routines.

`gh pr merge` and `git push --force` sit in `permissions.ask` rather than
`allow` or `deny`: possible when you invoke `merge-prs` or the commit
re-signing flow, never silent, and blocked by default in an unattended run
where nobody can answer the prompt.

## What's NOT here

- **Hooks.** No `PreToolUse` blockers, no `Stop` hook on failing tests. The
  state-machine preconditions in the skills cover the main drift scenarios.
- **Sub-specialised agents.** No separate Django / frontend / Celery agents.
  Single `implementer` until proven insufficient.
- **Autonomous merging or releasing.** `merge-prs` and `release` both exist,
  but both carry `disable-model-invocation: true` — you start them, never
  Claude.
- **Workflow logic in CLAUDE.md.** CLAUDE.md is reserved for taste (code
  style, conventions) and project facts. Workflow lives in the skills.

## Iterating

The skills are short on purpose. When you hit friction, ask:

- Friction in *what to do* → fix the skill
- Friction in *how the work is done* → fix the agent
- Friction repeated three times → consider a hook

Don't preemptively complicate.
