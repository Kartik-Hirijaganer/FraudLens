---
name: deadcode
description: Run the FraudLens dead-code sweep, classify findings against framework usage, and report cleanup candidates without making changes or recording Git history.
---

# Dead Code

Run the repository's advisory dead-code tooling and distinguish real cleanup from framework-driven
false positives.

## When To Use

Use when the user asks for dead-code analysis, unused-symbol review, or the `make deadcode` sweep.

## Rules

- Use the root Makefile target so Python and frontend detectors stay aligned with CI conventions.
- Advisory output is evidence to inspect, not automatic authorization to delete code.
- Account for FastAPI route registration, Pydantic model discovery, plugin hooks, and public exports.

## Steps

1. Read the relevant scope under `plans/` and run `make deadcode`.
2. Trace each vulture, ruff, and knip finding to imports, registrations, tests, and public exports.
3. Classify each item as a true positive, framework false positive, or uncertain.
4. If strict behavior was explicitly requested, run `DEADCODE_STRICT=1 make deadcode`.
5. Report evidence-backed cleanup candidates without modifying them.

## Verification

- Every reported true positive cites its file and symbol plus evidence that it has no live consumer.
- Framework false positives cite the registration or discovery path that keeps them live.
- The report states whether advisory or strict mode was run.

## Never Do

- Never delete or refactor code while executing this analysis-only skill.
- Never call an item dead solely because a text search found no direct caller.
- Never stage, commit, or push findings.
