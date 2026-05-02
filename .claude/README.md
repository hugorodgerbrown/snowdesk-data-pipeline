# Snowdesk Claude Code workflow

Four skills + three agents that take a Linear ticket from a one-line description through to a merged PR, with you in the loop at each handoff.

## Layout

```
.claude/
├── skills/
│   ├── scope/SKILL.md        ← Todo → Ready for dev
│   ├── start/SKILL.md        ← Ready → In Progress (branch + plan)
│   ├── implement/SKILL.md    ← In Progress (code + review loop)
│   └── raise-pr/SKILL.md     ← In Progress → In Review (PR opened)
└── agents/
    ├── scoper.md             ← used by /scope
    ├── implementer.md        ← used by /implement
    └── reviewer.md           ← used by /implement
```

Drop the whole `.claude/` directory at the root of your Snowdesk repo. Existing `.claude/` contents are not touched — these are new files.

## The flow

```
Backlog ──[you prioritise]──▶ Todo
                                │
                          "scope SNOW-42"
                                │
                                ▼
                            scope skill
                          (scoper agent)
                                │
                          [you approve]
                                │
                                ▼
                         Ready for dev
                                │
                          "start SNOW-42"
                                │
                                ▼
                            start skill
                       (branch + plan mode)
                                │
                          [you approve plan]
                                │
                                ▼
                          In Progress
                                │
                            "implement"
                                │
                                ▼
                          implement skill
                  (implementer ⇄ reviewer loop)
                                │
                                ▼
                          "raise the PR"
                                │
                                ▼
                          raise-pr skill
                                │
                                ▼
                            In Review
                                │
                          [you review/merge]
                                │
                                ▼
                              Done
```

Four English sentences from you, three approval gates (scope, plan, PR copy).

## Invoking

You don't need to remember slash commands. Auto-invocation matches the descriptions:

- "scope SNOW-42" → scope skill
- "let's start SNOW-42" → start skill
- "implement" / "go ahead and build it" → implement skill
- "raise the PR" / "ship it" → raise-pr skill

Slash commands (`/scope`, `/start`, `/implement`, `/raise-pr`) work as deterministic fallbacks.

## State machine guarantees

Each skill has a hard precondition on Linear ticket state:

| Skill      | Required state    | Transitions to    |
|------------|-------------------|-------------------|
| scope      | Todo              | Ready for dev     |
| start      | Ready for dev     | In Progress       |
| implement  | (no transition)   | (no transition)   |
| raise-pr   | In Progress       | In Review         |

If a ticket is in the wrong state, the skill stops and tells you why. This prevents skipping steps (e.g. starting work on a ticket that hasn't been scoped).

## What's NOT here

Deliberately omitted from this v1:

- **Hooks.** No `PreToolUse` blockers on git commit, no `Stop` hook on failing tests. The state-machine preconditions in the skills cover the main drift scenarios; hooks can be added later if you find process drift recurring.
- **Sub-specialised agents.** No separate Django / frontend / Celery agents. Single `implementer` until proven insufficient.
- **A merge skill.** Merging is your call.
- **Rules in CLAUDE.md.** None of the workflow logic lives in CLAUDE.md — it's all in the skills. CLAUDE.md should remain reserved for taste (code style, conventions) and never-changing project facts.

## Iterating

The skills are short on purpose. When you hit friction, the question to ask is:
- Friction in *what to do* → fix the skill
- Friction in *how the work is done* → fix the agent
- Friction repeated three times → consider a hook

Don't preemptively complicate.
