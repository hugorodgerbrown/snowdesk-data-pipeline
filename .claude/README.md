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
│   └── ticket-authoring-guide/ ← canonical rules for ticket create/scope
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
- **Action skills** — things you ask for — are imperative verb phrases: the
  name completes "Claude, …" (`scope`, `implement`, `work-on`, `merge-prs`,
  `post-project-update`). Keep the name as short as unambiguity allows; add
  the object as soon as the bare verb could mean two things — which is why
  the security audit is `audit-security` and the drift audit is `audit-code`,
  not a bare `audit` that competes with both.
- **Reference skills** — rulebooks Claude consults while doing something
  else — are noun phrases ending in `-guide` (`ticket-authoring-guide`).
  They are usually model-invoked, not typed.
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

  An agent nothing invokes and nobody asks for is dead weight — delete it
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
- **MCP grants name a configured server**: `mcp__linear-server`, not
  `mcp__linear`. A skill that a Routine runs remotely needs the connector UUID
  as well — see "Linear MCP permissions" below.
- **`disable-model-invocation: true` on anything with an irreversible side
  effect** (`release` deploys production, `merge-prs` squash-merges). It makes
  the skill human-only. Do **not** put it on a skill a Routine may fire —
  `post-project-update` and `audit-code` both support unattended mode, and
  the flag also blocks a scheduled task.

`user-invocable` defaults to `true`; set it to `false` only for a reference
skill that isn't a meaningful thing to type.

## Ticket lifecycle

```
Backlog ──▶ Todo ──"scope NN"──▶ Ready for dev ──"implement NN"──▶ In Progress
                      │                                │
                 (scope skill +                  (implement skill:
                  scoper agent)                   branch + plan gate,
                                                  implementer ⇄ reviewer,
                                                  push + PR)
                                                       ▼
                                  PR opened ──▶ In Review ──merge──▶ Done
                                                          (GitHub–Linear
                                                           integration)
```

`work-on NN` chains the two: scope (if the ticket is still in Todo) then
implement, in one session. Two approval gates either way: the scope and the
implementation plan.

## Auto-invocation phrases

You don't need to remember slash commands — say what you want:

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

| Skill     | Required state         | Transitions to              |
|-----------|------------------------|-----------------------------|
| scope     | Todo                   | Ready for dev               |
| implement | Ready for dev          | In Progress, then In Review |
| work-on   | Todo or Ready for dev  | In Review                   |

Tickets in the wrong state cause the skill to stop and explain why.

## post-project-update (special case)

`post-project-update` is the one skill that runs **unattended** — a scheduled
Routine invokes it once a day and posts the resulting status update to Linear
without an approval gate. The skill lives in this repo (not under `~/.claude/`)
so the remote Routine environment can find it after cloning.

When you invoke it interactively (e.g. "post a project update for Snowdesk"),
the approval gate is restored: draft → review → post.

## Linear MCP permissions

Every skill here talks to Linear over MCP, and the same Linear server reaches a
session under **two different names** depending on where the session runs:

| Name | Where it comes from | Which sessions see it |
|---|---|---|
| `linear-server` | Local config in `~/.claude.json`, project-scoped | Local sessions on this machine |
| `bee16520-…` (UUID) | A claude.ai **connector**, OAuth held against the Anthropic account | Local **and** remote/cloud sessions |

Permission rules match the literal string `mcp__<serverName>__<toolName>`, so a
rule written against one name does nothing for the other. `permissions.allow`
in [`settings.json`](settings.json) therefore lists the same nine tools twice,
once per name. The UUID is specific to this account's connector install; it is
committed because this repo is single-author, and it is the only string that
suppresses the approval prompt in a remote session.

**Do not add a `.mcp.json` for Linear.** It would declare an OAuth-only server
that a remote container cannot authenticate (the OAuth flow needs an
interactive session), and project-scoped servers carry their own first-use
trust prompt on top. Remote access already works via the connector. A Linear
API key must never be committed here, including through `${VAR}` interpolation
— see invariant 5 in [`CLAUDE.md`](../CLAUDE.md).

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
