# /snow-issue — Create or scope a Snowdesk Linear ticket

Use this command to create a new `SNOW-` ticket **or** to add a scoping comment
to an existing one. It enforces the scoping-comment contract from
`docs/linear-workflow.md` before the ticket can move to `Ready for dev`.

## Workflow

### If creating a new ticket

1. Ask the user for:
   - A one-line title (imperative: "Add X", "Fix Y", "Remove Z")
   - A brief description of the problem or goal (2–4 sentences)
   - Type: `feature`, `bug`, or `chore`

2. Create the ticket in Linear:
   - Team: `SNOW`
   - Status: `Todo`
   - Estimate: t-shirt size — ask if not obvious from the description
     (`XS` < 1 h, `S` < half-day, `M` < 1 day, `L` < 3 days, `XL` > 3 days)

3. Proceed to the **Scoping** step below.

### If scoping an existing ticket

If the user passes a ticket identifier (e.g. `/snow-issue SNOW-42`), fetch that
ticket and its comments via the Linear MCP, then proceed straight to scoping.

### Scoping

Explore the codebase to answer the four questions in the scoping-comment
contract, then post the comment on the Linear ticket:

```
**Approach** (2–4 sentences on implementation strategy)

**Touch list** (files / modules that will change)
- …

**Tests** (what will be covered)
- …

**Open questions** (if any — leave blank if none)
- …
```

If there are open questions, leave the ticket at `Todo` and surface the
questions to the user before moving on.

If there are no open questions, move the ticket to `Ready for dev` and confirm
to the user that it is ready for `/linear-ticket-implementer` or
`/snow-pr` after implementation.

## Conventions (baked in — do not ask the user)

- Team prefix is always `SNOW`
- One ticket per branch — warn if the current branch already has a different
  `SNOW-` ticket associated with it
- British English in ticket titles and descriptions
- Scoping comment is always posted as a comment, not edited into the ticket
  description
