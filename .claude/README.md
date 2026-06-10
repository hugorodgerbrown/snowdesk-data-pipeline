# Snowdesk Claude Code workflow

Skills and agents for taking a Linear ticket from a one-line description
through to a merged PR, plus a few utilities (security audit, project status
updates).

## Layout

```
.claude/
├── skills/
│   ├── scope/                       ← Todo → Ready for dev
│   ├── implement/                   ← Ready for dev → In Progress → PR open
│   ├── work-on/                     ← scope + implement chained in one session
│   ├── audit/                       ← security audit (security-auditor agent)
│   ├── post-project-update/         ← Linear project status update (used by Routine)
│   ├── ticket-authoring-guide/      ← canonical rules for ticket create/scope
│   └── ticket-implementation-guide/ ← canonical rules for ticket pickup
└── agents/
    ├── scoper.md                    ← used by scope
    ├── implementer.md               ← used by implement
    ├── reviewer.md                  ← used by implement
    ├── qa.md                        ← on-demand manual-testing docs
    ├── documenter.md                ← on-demand doc maintenance
    └── security-auditor.md          ← used by audit
```

## The two layers

| Concept | What it is | How invoked | Where it lives |
|---|---|---|---|
| **Agent** | Subprocess with its own system prompt, tool allowlist, and model. Returns one message. | `Agent(subagent_type=…)` — usually by a skill | `.claude/agents/<name>.md` |
| **Skill** | A reusable playbook. Description carries trigger phrases; the body loads only when invoked. | Typed as `/<name>`, or auto-invoked when the user's phrasing matches the description | `.claude/skills/<name>/SKILL.md` |

(There used to be a third layer — `.claude/commands/` slash commands — but
commands and skills are now a unified mechanism, and the one command,
`snow-audit`, has been converted to the `audit` skill.)

## Skill naming convention

- **kebab-case, lowercase.**
- **No namespace prefixes** (`snow-`, `linear-`): project skills are already
  scoped to this project. Prefixes are for plugins.
- **Action skills** — things you ask for — are imperative verb phrases: the
  name completes "Claude, …" (`scope`, `implement`, `work-on`, `audit`,
  `post-project-update`). Keep the name as short as unambiguity allows; add
  the object only when the bare verb could mean two different things.
- **Reference skills** — rulebooks Claude consults while doing something
  else — are noun phrases ending in `-guide` (`ticket-authoring-guide`,
  `ticket-implementation-guide`). They are usually model-invoked, not typed.

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
- "run a security audit" → audit
- "post a daily update for Snowdesk" → post-project-update

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

## What's NOT here

- **Hooks.** No `PreToolUse` blockers, no `Stop` hook on failing tests. The
  state-machine preconditions in the skills cover the main drift scenarios.
- **Sub-specialised agents.** No separate Django / frontend / Celery agents.
  Single `implementer` until proven insufficient.
- **A merge skill.** Merging is your call.
- **Workflow logic in CLAUDE.md.** CLAUDE.md is reserved for taste (code
  style, conventions) and project facts. Workflow lives in the skills.

## Iterating

The skills are short on purpose. When you hit friction, ask:

- Friction in *what to do* → fix the skill
- Friction in *how the work is done* → fix the agent
- Friction repeated three times → consider a hook

Don't preemptively complicate.
