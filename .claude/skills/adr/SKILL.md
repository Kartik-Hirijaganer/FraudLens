---
name: adr
description: Create or update a FraudLens Architecture Decision Record in the house format, including its index row, active-plan pointer, evidence, trade-offs, and reconsideration criteria.
---

# Architecture Decision Record

Capture a durable architectural decision without duplicating it across plans and docs.

## When To Use

Use when a plan requires an ADR, an architectural choice becomes durable, or an existing accepted
decision needs a superseding record.

## Rules

- ADRs live under `docs/architecture/adr/` and use the next assigned `ADR-NNN-kebab-title.md` name.
- Use the house order: Context, Decision, Why, Options considered and rejected, Tradeoffs accepted,
  Reconsider when.
- Add exactly one row to `docs/architecture/adr/README.md` and a relative pointer to the relevant
  implementation plan under `plans/`.
- Record the real status and evidence. Do not call a planned or validated-only state demonstrated.
- Amend an accepted ADR only for clarification; supersede it when the decision changes.

## Steps

1. Read the active plan, ADR index, and adjacent records to determine number, scope, and conflicts.
2. State the decision in one testable sentence and identify boundaries it does not change.
3. Document considered options, concrete reasons, operational/security costs, and triggers for
   revisiting the choice.
4. Add related ADR and plan links using repository-relative paths.
5. Add the index row with status/date and run `make docs-links-check` plus `make docs-check`.

## Verification

- Filename, title, index number, status, date, and link targets agree.
- Every option and trade-off is decision-specific and the reconsideration criteria are actionable.
- Security, tenant isolation, PHI posture, cost, and compatibility are addressed when applicable.
- The plan points to the ADR as the canonical decision record rather than restating it indefinitely.

## Never Do

- Never invent implementation evidence or retroactively change a decision date.
- Never silently overwrite or renumber an accepted ADR.
- Never duplicate the canonical decision across multiple ADR files.
- Never commit, push, or tag without explicit permission.
