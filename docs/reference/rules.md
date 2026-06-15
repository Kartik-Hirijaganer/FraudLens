# AML rules engine reference

> The deterministic AML/fraud **rules engine** (plan §10.2, §16 Phase 4). Pure, framework-free
> logic lives in [`fraudlens_core.rules`](../../packages/fraudlens-core/src/fraudlens_core/rules);
> the tenant-facing CRUD surface is [`api/v1/rules.py`](../../backend/src/fraudlens_backend/api/v1/rules.py)
> (endpoint 14) backed by [`RuleRepository`](../../backend/src/fraudlens_backend/db/repositories/rules.py).
> The engine produces typed, **PHI-free** hits plus a weighted subscore that later phases combine
> with the model score (§10.1). This page is hand-maintained.

## What the engine does

For one transaction it evaluates a set of rule definitions against a PHI-free `RuleContext`
(the transaction under review plus same-account history, **pre-grouped by the caller** so no
account identifier ever reaches the engine) and returns a `RuleEvaluation`:

- `hits` — the rules that fired, each a `RuleHit` with `code`, `ruleType`, `severity`, `weight`,
  a fixed **PHI-free** `reason`, and numeric `details` (counts / thresholds / non-PHI country).
- `subscore` — a deterministic value in **[0, 1]** (see [Aggregation](#aggregation)).
- `rulesVersion` — a fingerprint of the evaluated rule set; changes when any rule's `version`
  or `enabled` flag changes (so a run's recorded `rules_version` reflects the exact rule set).
- `erroredRules` — codes of rules skipped by [fault isolation](#fault-isolation).

## Built-in rules

The baseline rule set (one per type) lives in the single canonical
[`DEFAULT_RULE_DEFINITIONS`](../../packages/fraudlens-core/src/fraudlens_core/rules/builtins.py),
which the seed loads into `aml_rules` as **global** rows and the engine merges DB overrides onto.
Every business value is a `params` tunable — nothing is hardcoded (rule 4).

| Type (`ruleType`) | Fires when… | Default `params` | Severity | Weight |
|---|---|---|---|---|
| `structuring` | ≥ `minCount` sub-threshold transactions (current + history) cluster within `windowHours` | `threshold=10000`, `marginPct=0.1`, `windowHours=168`, `minCount=3` | high | 2.0 |
| `velocity` | transaction count within `windowHours` exceeds `maxCount` | `windowHours=24`, `maxCount=5` | medium | 1.0 |
| `high_risk_geography` | the transaction `country` is in `countries` | `countries=["IR","KP","SY","CU","RU"]` | high | 1.5 |
| `round_amount` | the amount is a positive whole multiple of `multipleOf` | `multipleOf=1000` | low | 0.5 |
| `threshold_evasion` | a single amount sits in `[threshold·(1−marginPct), threshold)` | `threshold=10000`, `marginPct=0.1` | high | 2.0 |
| `rapid_movement` | an outbound follows a comparable inbound (≥ `minRatio`) within `windowHours` | `windowHours=48`, `minRatio=0.8` | medium | 1.5 |

The `countries` list is synthetic/illustrative and fully configurable; it is **not** a sanctions
list. Each evaluator falls back to these defaults when a param is missing or malformed, so a
custom rule with partial `params` still behaves sensibly.

## Rule resolution (merge precedence)

`RuleRepository.load_definitions` builds the effective rule set by layering, lowest precedence
first, keyed by `code`:

1. **Code defaults** — `DEFAULT_RULE_DEFINITIONS` (always present, so rules work even if the
   `aml_rules` table is empty or unavailable — graceful degradation, §11).
2. **Global DB rows** (`agency_id IS NULL`) — the seeded platform baseline.
3. **This agency's rows** (`agency_id = <tenant>`) — per-agency overrides win.

An agency customizes a baseline rule by creating an agency-scoped rule with the **same `code`**.

## Aggregation

```
subscore = (Σ weight of rules that FIRED) ÷ (Σ weight of rules that EVALUATED CLEANLY)
```

quantized to 4 decimals and bounded to [0, 1]. Disabled and faulted rules are excluded from
**both** sides, so the score depends only on the rules that actually ran (deterministic, and a
transient rule failure neither inflates nor dilutes it). With no evaluable rules the subscore is 0.

## Fault isolation

Each rule is evaluated independently: if an evaluator raises, its `code` is recorded in
`erroredRules` and the rest of the run proceeds — a buggy rule can never abort an investigation
(plan §16 Phase 4). A definition whose `ruleType` has no registered evaluator is treated the same
way (skipped, not raised).

## CRUD API (endpoint 14)

`/api/v1/rules` is **agency-scoped** like every business route — every operation is bound to the
verified JWT `agency_id`, and a cross-tenant (or global/baseline) id resolves to **404** with no
existence leak (§6.4). The seeded global baseline rules are platform rows and are not editable
through this tenant surface.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/rules` | List the agency's own rules (ordered by `code`). |
| `POST` | `/api/v1/rules` | Create an agency rule (201; 409 `duplicate_rule_code`). `code` + `ruleType` are immutable. |
| `GET` | `/api/v1/rules/{ruleId}` | Detail (404 `rule_not_found`). |
| `PATCH` | `/api/v1/rules/{ruleId}` | Partial update incl. **enable/disable**; bumps `version`. |
| `DELETE` | `/api/v1/rules/{ruleId}` | Remove the agency's rule (204; 404 otherwise). |

`params` is a free-form camelCase JSON object; values are coerced defensively by the engine and
never echoed in errors. Error codes are catalogued in
[configuration.md](configuration.md#error-code-catalog).
