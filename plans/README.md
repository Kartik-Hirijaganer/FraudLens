# Plans

Implementation plans for FraudLens. **Golden Rule 3:** every non-trivial change starts
with a plan here.

## Naming

```
plans/YYYY-MM-DD-<short-kebab-title>.md
```

Examples:

- `plans/2026-06-09-initial-repo-setup.md`
- `plans/2026-07-01-transaction-scoring-pipeline.md`

The date is the day the plan is created; keep the title short and specific.

## Structure

Use `## Phase N — <name>` headings so the **drift-check** skill can audit phase by phase:

```markdown
# <Title>

## Context
Why this work; links to docs/tickets.

## Phase 1 — <name>
- [ ] concrete, verifiable steps

## Phase 2 — <name>
- [ ] ...
```

## Auditing a plan

After implementing, run a read-only drift audit:

```
drift-check plans/<file>.md phase=<N>     # one phase
drift-check plans/<file>.md all           # every phase
```

drift-check validates actual repo state against the plan and the governance rules in
[AGENTS.md](../AGENTS.md) (PHI, `agency_id` scoping, JWT validation).
