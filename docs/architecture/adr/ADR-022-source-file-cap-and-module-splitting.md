# ADR-022 — Source files: absolute 500-line cap with responsibility-based module splits

- **Status:** Accepted
- **Date:** 2026-09-13
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  [`plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md`](../../../plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md)

## Context

FraudLens accumulated 33 source and test files above 500 physical lines. Those files mixed
configuration, persistence, transport, policy, presentation, and test scaffolding, which made
ownership unclear and raised the cost of review. A temporary shrink-only baseline allowed the cap
to land before all files could be split, but keeping that exception would turn a transition aid into
permanent architectural debt.

The repository also has stable Python import paths, TypeScript module specifiers, CLI entry paths,
and monkeypatch seams. A mechanical size reduction that silently moved those boundaries would be a
behavioral change, not a safe refactor. The policy therefore needs to constrain both file size and
how a split preserves compatibility.

## Decision

Every governed Python, TypeScript, and TSX source file must contain at most 500 physical lines, as
measured by `make file-length-check`, with no committed baseline or grandfathered offender. Alembic
migration history and generated files remain exempt because editing historical migrations or
splitting generated output would reduce reproducibility rather than improve ownership.

Oversized modules are split along responsibility boundaries into named sibling modules or packages.
Established imports, CLI paths, and test seams remain compatible through thin facades, Python
package `__init__.py` files, or TypeScript barrels. A non-`__init__` Python facade declares an
explicit `__all__` and avoids wildcard imports; TypeScript barrels may use `export *` to preserve
their established module specifier. Test modules split by behavior topic and move reusable doubles
or fixtures into shared test-support modules without changing assertions.

The absolute gate covers `backend/src`, `packages`, `frontend/src`, `scripts`, `tests`, and
`alembic`. Script-module coverage is a separate `make scripts-test` gate at 90% aggregate branch
coverage. Copy/paste detection includes `scripts`, and Python dead-code discovery explicitly covers
`packages/fraudlens-llm/src` so module extraction cannot hide unused implementations.

This decision changes maintainability constraints only. It does not change API contracts, tenant
scope, authorization, PHI handling, provider access, cloud deployment, experiment spending, or the
human approval boundaries around SARs and cloud mutations.

## Why

**1 · An absolute cap prevents exception drift.** A baseline can ratchet known debt down, but it also
normalizes files that remain harder to review. Removing it makes the same rule apply to every new
and existing source file.

**2 · Responsibility names make boundaries reviewable.** Modules named for contracts, transport,
policy, persistence, or presentation communicate why code belongs together. Arbitrary numbered
fragments would satisfy a counter without improving architecture.

**3 · Compatibility facades separate structure from behavior.** Existing callers keep their import
or executable path while implementations move behind an explicit surface. This allows incremental
refactoring without forcing unrelated consumers to change in the same commit.

**4 · Tests and tooling close the regression path.** Shared fixtures reduce copied setup, script
coverage protects code that the main application coverage configuration does not measure, and
dead-code plus duplication checks catch extraction that merely relocates unused or repeated code.

## Options considered and rejected

1. **Keep the shrink-only baseline indefinitely** — rejected because compliant files would still be
   represented as exceptions and future cleanup could stall without failing the build.
2. **Raise the limit** — rejected because it reduces gate failures without addressing mixed
   responsibilities or review cost.
3. **Count logical statements instead of physical lines** — rejected because the metric would vary
   by parser and language, and could be gamed through formatting. Physical lines match `wc -l` and
   are deterministic across Python, TypeScript, and TSX.
4. **Split files without compatibility facades** — rejected because dozens of consumers and CLI
   entry paths would move at once, increasing regression risk with no product benefit.
5. **Exempt tests or scripts** — rejected because oversized fixtures and operational scripts carry
   substantial behavior, security, and publication logic; reviewability matters there too.

## Tradeoffs accepted

- The repository contains more files and import edges. Responsibility names, explicit exports, and
  dependency checks are accepted as the cost of smaller review units.
- Facades add a thin indirection layer. They preserve stable public paths and make future removals
  deliberate, but contributors must avoid putting unrelated behavior back into them.
- Aggregate script coverage can mask a locally weak module. Focused behavioral suites and the main
  changed-file coverage gate mitigate that risk; critical modules may gain stricter focused gates.
- Physical-line limits can prompt premature splitting near the threshold. The 500-line ceiling is a
  guardrail, not a target; cohesion and clear ownership still decide the seam.

## Reconsider when

- The repository adopts a language or generated-source workflow that cannot be measured reliably by
  the current deterministic file discovery.
- Repeated facade cycles or import-layer violations show that package boundaries, rather than file
  boundaries, need a broader architectural change.
- Empirical review data supports a different limit while preserving an absolute, baseline-free gate;
  changing the number requires a superseding ADR and coordinated tooling update.
- A critical operational area needs per-module rather than aggregate coverage; tighten that target
  without weakening the repository-wide source cap.

**Never** satisfy the cap with numbered fragments, wildcard facades, compressed formatting, removed
tests, or hidden exclusions. The split must preserve behavior and make ownership clearer.
