---
name: drift-check
description: Run strict read-only FraudLens drift audits against a plan and phase. Use for drift-check, phase audit, gap analysis, redundancy, and implementation validation.
---

# Drift Check

Conduct a strict, evidence-driven audit. Do not implement fixes, refactor, or suggest diffs. Validate actual repository state against the plan and report drift.

## When To Use

Use when the user asks for a drift check, plan-vs-code audit, implementation validation, phase audit, gap analysis, redundancy check, or mentions drift-check.

## Inputs

Parse the user's request as `<plan-path> <phase|all>`.

Accepted forms:

- `<plan-path> <phase>` such as `.agents/plans/foo.md phase-3`
- `<plan-path> phase=<N>` such as `.agents/plans/foo.md phase=6`
- `<plan-path> all` to validate every phase in the plan
- `<plan-path>` alone: ask which phase to validate; do not guess

If the user typed an old slash form such as `/drift-check <plan-path> phase=3`, ignore the `/drift-check` token and parse the remaining text. If no plan path is provided, stop and ask for one. Never invent a plan or pick one by scanning plan directories.

## Core Rules

- Read-only. Do not write, edit, delete, stage, commit, apply patches, run autofix, run formatters, run migrations, or deploy.
- Evidence required. Every claim must cite a file path, line number, symbol, API route, schema field, test name, or captured command output.
- The plan is the contract. Do not grade whether the plan was wise; grade implementation against it.
- No vague language. Drop unverifiable claims.
- Follow FraudLens governance: no PHI in logs, URLs, errors, or query params; every tenant-scoped database query and background job must be scoped by `agency_id`; JWT `agency_id` claims must be validated against requested resources.
- Use code-review-graph MCP tools before grep/read where available. Fall back to ripgrep and file reads for markdown, fixtures, generated docs, configs, or artifacts the graph does not cover.

## Workflow

1. Load the plan and scope.
   - Read the full plan at the supplied path.
   - Parse phase headings and isolate the requested phase or every phase when `all` is requested.
   - Capture the goal, demo criteria, files, symbols, routes, schemas, tests, configs, non-goals, and compatibility promises.
   - Before reading implementation code, restate the intended phase changes in 2-4 bullets as ground truth.

2. Map plan items to implementation artifacts.
   - For every named file, symbol, route, schema field, test, fixture, migration, and config key, locate the current artifact.
   - Capture definition, callers, callees, imports, tests, fixtures, migrations, generated docs, and config usage.
   - Build an internal mapping table: `Plan item | Expected artifact | Observed artifact | Match?`.

3. Gather evidence.
   - Code-level changes: verify definitions, signatures, placement, defaults, and call sites.
   - Tests: confirm promised tests exist, are collected, and assert the new behavior; stubs, skips, and unrelated assertions do not count.
   - API contracts: compare server schemas, client types, OpenAPI, field casing, optionality, defaults, error envelope, auth, and idempotency.
   - Data models: verify field additions/removals, migrations, defaults, relationships, indexes, round trips, and generated ERD freshness.
   - Workflow/UI flow: trace full UI -> API -> library/background-job paths for planned demos or user-visible flows.
   - Backward compatibility: confirm explicit tests for promised pre-patch behavior.
   - Static gates: run relevant lint, typecheck, and test targets read-only, no autofix.
   - Release/hygiene gates: if release metadata changed, inspect Makefile/package scripts and run read-only checks when safe.

4. Apply FraudLens governance gates.
   - HIPAA/PHI: no patient names, SSNs, diagnoses, DOB, zip codes, or similar PHI in logs, diagnostics, URLs, query params, or raw errors; PHI-touching endpoints must audit log.
   - Multi-tenant isolation: every database query and background job in scope filters by `agency_id`; resource access validates requested agency against token claims.
   - Banned names: flag merged symbols or files containing `v2`, `new_`, `temp_`, `tmp_`, `old_`, `legacy_`, `copy_`, or `_refactored`.
   - Mandatory cleanup: anything the plan said to delete or replace must be gone; dead code left behind is drift.
   - Generated docs: if endpoints, routers, service `main.py`, or SQLAlchemy models changed, verify `docs/reference/generated/api/` and `docs/reference/generated/erd/` are regenerated and in sync.
   - Alembic: verify exactly one Alembic head when migrations are in scope.

## Decision Logic

Judge alignment only against the supplied plan and repository evidence. Report `Aligned` when every required plan item is present and governed, `Partially Aligned` when non-blocking drift or gaps remain, and `Misaligned` when core behavior, architecture, data, API, or security requirements are missing.

## Output Format

Output these sections exactly, in this order. Keep each heading even when empty; write `None observed.` for sections with zero findings.

### 1. Executive Summary

- One-line verdict: **Aligned** / **Partially Aligned** / **Misaligned**.
- Phase(s) audited, plan path, current branch, and `git rev-parse --short HEAD`.
- Coverage: `X of Y` plan items verified present and correct.
- 3-6 bullets on highest-impact risks.
- Blocking release issues, if any.

### 2. Drift Analysis

For each deviation:

- **Planned Behavior** - direct quote or tight paraphrase from the plan with plan line or section reference.
- **Actual Implementation** - current behavior with file/line citation.
- **Nature of Drift** - one of: functional / architectural / data / API / event / security / UI.
- **Impact Assessment** - low / medium / high, with a one-line reason.

### 3. Gap Analysis

- Missing features or incomplete components with file/line where the gap should live.
- Unimplemented plan sections with quote or section anchor.
- Broken or partially implemented workflows, traced through the broken path.
- Evidence of absence: tool/query and null result, or partial artifact citation.

### 4. Redundancy & Duplication

- Duplicate APIs, services, modules, loops, schemas, agents, models, tables, or workflows.
- Overlapping responsibilities between new and existing components; cite both.
- Parallel implementations that should have replaced the original; cite AGENTS.md or AGENTS.md "No parallel implementations".
- Banned naming patterns found in merged code: `v2`, `new_`, `temp_`, `tmp_`, `old_`, `legacy_`, `copy_`, `_refactored`.

### 5. Data Model & Schema Validation

- Planned vs actual tables, columns, types, nullability, defaults, relationships, cascade rules, and indexes.
- Tenancy: `agency_id` present and indexed on every new tenant-scoped table; Row-Level Security policy present if required.
- Migrations: exactly one Alembic head, migration files match ORM models, no autogenerate drift.
- Schema drift between server models, client types, fixtures, library dataclasses, and generated artifacts.
- Unexpected public export changes such as `__all__`, `index.ts`, or other barrel exports.
- ERD freshness in `docs/reference/generated/erd/` when models changed.

### 6. API Contract Validation

- Path and method under the correct `/api/v1/` prefix unless the plan explicitly requires otherwise.
- Request/response schemas match the plan and FraudLens casing convention: camelCase API surface, snake_case Python internals.
- Pydantic validation, auth/scope checks, and `agency_id` claim validation are present.
- Idempotency keys are present on critical operations where required.
- Errors follow the FraudLens envelope: `code`, `message`, `details`, `requestId`; no stack traces or raw exception names leak.
- OpenAPI in `docs/reference/generated/api/` is regenerated and committed.
- Public library re-exports did not introduce breaking drift.

### 7. Dead Code & Cleanup Candidates

- Unused files, symbols, fixtures, tests, services, routers, jobs, feature flags, handlers, DB tables, or columns.
- Legacy paths the plan said to remove but that remain.
- Stub functions, TODO markers, commented-out blocks, uncollected tests, zero-call graph results, or orphaned imports.

### 8. Risk Register

Use this table:

| # | Description | Affected Components | Severity (L/M/H/Critical) | Recommended Action (advisory only - no code) |
|---|-------------|---------------------|----------------------------|----------------------------------------------|

HIPAA/PHI exposure, multi-tenant isolation gaps, and missing audit logs default to `Critical`.

## Final Line

End the audit with one line in this exact format:

```
AUDIT VERDICT: <Aligned | Partially Aligned | Misaligned> — <N> drift, <M> gaps, <K> duplications, <D> dead-code items, <R> risks (<critical-count> critical).
```

## Failure Modes To Avoid

- Citing the plan as implementation evidence.
- Reporting green status because a test file exists without confirming assertions.
- Calling something duplicated without citing both locations.
- Locating a symbol with grep and declaring completion without reading the source.
- Running any write-capable or destructive command.
