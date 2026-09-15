---
name: split-module
description: Split an oversized FraudLens source or test module below the 500-line cap while preserving behavior, public import paths, monkeypatch seams, explicit exports, test coverage, and architectural layering.
---

# Split Module

Perform a behavior-preserving module split along responsibility-named seams.

## When To Use

Use for Phase 2 line-cap work or any source module approaching the 500-physical-line limit.

## Rules

- This is a structural move: preserve behavior, API contracts, persisted schemas, imports, and test
  assertions.
- Name siblings by responsibility; banned migration-style names remain forbidden.
- Prefer a package plus `__init__.py` where the import path allows it. When the original file must
  remain, make it a narrow facade with an explicit `__all__`.
- Preserve monkeypatch targets and imported module aliases deliberately.
- Target no more than 400 lines per new module so ordinary maintenance has headroom.

## Steps

1. Read the exact split map and constraints in the active Phase 2 plan under `plans/`.
2. Map definitions, importers, runtime registrations, private cross-module uses, test patches, CLI
   entry points, and public exports before moving code.
3. Choose cohesive seams, promote cross-module names only when the plan requires it, and define the
   facade/barrel surface explicitly.
4. Move code without rewriting behavior. Split tests by topic and update explicit Makefile test lists
   in the same change.
5. Run the narrow affected tests after each move, then `make docs`, `make ci`, `make dup-check`, and
   `make deadcode`.
6. Remove the file-length baseline entry only when the original and every sibling comply.

## Verification

- Public imports and CLI paths remain valid; explicit `__all__` or TypeScript barrels match the
  intended surface.
- Monkeypatch-based tests still intercept the caller's actual lookup location.
- Every source file is at most 500 physical lines and newly split modules are near the 400-line
  target.
- Behavior tests, changed-line coverage, layering lint, duplication, dead-code, headers, and docs are
  green.

## Never Do

- Never combine feature work or schema changes with a behavior-preserving split.
- Never leave a parallel implementation, compatibility shadow, wildcard export, or stale baseline.
- Never rewrite tests so they stop asserting pre-split behavior.
- Never use banned names such as versioned, temporary, old, legacy, copied, or refactored variants.
