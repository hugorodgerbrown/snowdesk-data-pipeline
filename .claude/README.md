# Snowdesk Claude Code workflow

Skills, agents, and a slash command for taking a Linear ticket from a one-line description through to a merged PR, plus a few utilities (security audit, project status updates).

## Layout

```
.claude/
├── skills/
│   ├── scope/SKILL.md              ← Todo → Ready for dev
│   ├── start/SKILL.md              ← Ready → In Progress (branch + plan)
│   ├── implement/SKILL.md          ← In Progress (code + review loop)
│   ├── raise-pr/SKILL.md           ← In Progress → In Review (PR opened)
│   ├── linear-ticket-author/       ← canonical rules for ticket create/scope
│   ├── linear-ticket-implementer/  ← canonical rules for ticket pickup
│   └── project-update/             ← Linear project status update (used by Routine)
├── agents/
│   ├── scoper.md                   ← used by scope
│   ├── implementer.md              ← used by implement
│   ├── reviewer.md                 ← used by implement
│   ├── qa.md                       ← used by raise-pr
│   ├── documenter.md               ← on-demand doc maintenance
│   └── security-auditor.md         ← used by /snow-audit
└── commands/
    └── snow-audit.md               ← /snow-audit slash command
```

## The three layers

| Concept | What it is | How invoked | Where it lives |
|---|---|---|---|
| **Agent** | Subprocess with its own system prompt, tool allowlist, and model. Returns one message. | `Agent(subagent_type=…)` — usually by a skill | `.claude/agents/<name>.md` |
| **Skill** | A reusable playbook. Description carries trigger phrases; the body loads only when invoked. | `Skill(skill=…)` — auto-invoked when the user's phrasing matches | `.claude/skills/<name>/SKILL.md` |
| **Command** | A slash command — a prompt template the main agent reads inline. | User types `/snow-audit` etc. | `.claude/commands/<name>.md` |

Skills are implicit (the model decides to invoke based on trigger phrases); commands are explicit (user types `/<name>`). Both can delegate to agents.

## Ticket lifecycle

```
Backlog ──▶ Todo ──"scope SNOW-NN"──▶ Ready for dev
                       │
                  (scope skill +
                   scoper agent)
                       ▼
              "start SNOW-NN" ──▶ In Progress (branch + plan)
                       │
                  (start skill)
                       ▼
                "implement" ──▶ (code + review loop)
                       │
                  (implement skill;
                   implementer ⇄ reviewer)
                       ▼
              "raise the PR" ──▶ In Review
                       │
                  (raise-pr skill +
                   qa agent gate)
                       ▼
                     Done  (on merge, via GitHub-Linear integration)
```

Four English sentences from you. Three approval gates: scope, plan, PR copy.

## Auto-invocation phrases

You don't need to remember slash commands — say what you want:

- "scope SNOW-42" → scope
- "let's start SNOW-42" → start
- "implement" / "go ahead and build it" → implement
- "raise the PR" / "ship it" → raise-pr
- "post a daily update for Snowdesk" → project-update
- `/snow-audit` (explicit) → security-auditor

## State machine guarantees

Each skill enforces a hard precondition on Linear ticket state:

| Skill      | Required state    | Transitions to    |
|------------|-------------------|-------------------|
| scope      | Todo              | Ready for dev     |
| start      | Ready for dev     | In Progress       |
| implement  | (no transition)   | (no transition)   |
| raise-pr   | In Progress       | In Review         |

Tickets in the wrong state cause the skill to stop and explain why.

## project-update (special case)

`project-update` is the one skill that runs **unattended** — a scheduled
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
