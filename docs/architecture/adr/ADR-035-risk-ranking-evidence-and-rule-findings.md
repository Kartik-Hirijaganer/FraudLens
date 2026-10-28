# ADR-035 — Retain the served risk policy pending transferable ranking evidence

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

FraudLens started with a configurable 70% model-risk / 30% deterministic-rule blend and a 0.60
alert threshold. This was a heuristic policy, not a weight selected by the original model's
training experiment. Model risk is a piecewise-linear transformation of calibrated probability;
the transformation itself depends on the blend weight. SHAP explains the XGBoost raw margin.
These quantities cannot be presented as interchangeable probabilities or contributions.

The [ranking-policy review](../../reference/benchmarks/risk-ranking-review.md) verifies the later
HI-Medium experiment and identifies the production registry's active bundle as the earlier
HI-Small model. Current-code replay favors model-only on the Medium datasets, while 70/30 detects
17 more positive transactions than model-only on the served model's reconstructed account-whole
HI-Small evaluation. These are descriptive, source-dependent results. The original HI-Small
metadata metric is not exactly reproduced, and its operating points came from holdout.

Medium data extends beyond the served model's training period; a retrospective out-of-time slice
is reported. Its dates were already included in inspected aggregate comparisons. It is not a
new blind final test. The review records this correction and does not relabel it as untouched.

### Options considered

1. **Switch directly to model-only.** It is a credible candidate and wins calibration comparisons,
   but the served-source holdout reverses the direction. A global review-budget result also does
   not validate the fixed online threshold or risk-band changes from model-only normalization.
2. **Switch to 80/20 using the first HI-Medium report.** The report used a different bundle and
   informed subsequent analysis; it does not establish a transferable served-policy optimum.
3. **Add mandatory rule overrides.** No supported business policy requires them. A pattern match
   is not a fraud determination, and adding overrides would introduce a separate unvalidated
   alert policy.
4. **Keep the current default, expose findings clearly, and make comparison reproducible.** This
   preserves the demo's established behavior while documenting its limits and the next evidence
   needed for a change.

## Decision

Retain the configurable 70/30 default, 0.60 alert threshold and existing risk bands for the served
demo; preserve explicit rule findings and evaluate model-only as a candidate through the committed
offline study harness before changing the serving policy.

### Why

The evidence does not support one universal weight. In the served-source replay, model-only gains
9 positive cases and loses 26 that the blend selects at the same workload. Those lost cases have
explicit rapid-movement, geography, velocity, structuring or threshold-evasion findings. The flags
have a concrete investigation purpose: show the matched predicate and window, preserve its rule
version and reason, and supply a traceable observation to the analyst and SAR evidence catalog.
They remain distinct from SHAP model drivers and do not establish illicit activity.

Retaining the default is a bounded compatibility decision, not a finding that 70/30 is optimal.
The new [configuration](../../../config/risk-ranking.yaml),
[runner](../../../scripts/evaluate_risk_policy.py) and
[runbook](../../runbooks/risk-ranking-evaluation.md) compare frozen models, actual scalar-policy
semantics, equal review budgets, fixed thresholds, calibration-selected thresholds, exchanged
cases, rule patterns and daily windows. They do not retrain or move deployment pointers.

The user interface labels calibrated probability separately from policy risk score, identifies
SHAP as raw-margin contributions, and displays rule findings in both investigation and alert
review even when SAR drafting is unavailable. API field names remain compatible; their schema
descriptions clarify these meanings. No hard rule override is introduced.

## Tradeoffs accepted

- Some evaluated sources favor model-only; retaining the heuristic may forgo ranking gains there.
  Those gains are recorded rather than hidden or generalized to the served model.
- The original served bundle's holdout-derived thresholds and historical metric mismatch remain
  visible limitations. The review does not rewrite the original metadata or 70/30 experiment.
- Fixed 0.60 alerts do not enforce an exact 5% queue budget. Calibration-derived cutoffs also show
  workload drift; adopting a capacity policy requires a separately specified operational design.
- The product queue groups severity and recency; it is not an online top-5% ranking system. Offline
  ranking metrics cannot be described as end-to-end queue performance.
- This work changes local explanations, tests, research tooling and documentation only. Tenant
  queries, JWT authorization, human maker/checker decisions, egress controls and SAR quality gates
  keep their existing contracts. No cloud resources, secrets, deployments or Git history change.

## Reconsider when

- A predeclared comparison on a relevant, unexamined future period supports a candidate across
  time windows and case types, with suitable uncertainty analysis and an explicit review budget.
- The actual serving bundle changes; repeat the comparison and bind both calibration metadata
  and booster bytes before transferring any result.
- The historical HI-Small metric discrepancy is resolved against its original feature/prediction
  artifacts, or a new model with calibration-only operating points supersedes that bundle.
- A supported business policy calls for an explicit rule-triggered review path; evaluate it
  separately from a convex ranking blend and record its workload and case coverage.

Related: [ADR-018](ADR-018-portfolio-demo-data-provenance.md),
[ADR-023](ADR-023-sar-quality-and-privacy-gates.md),
[ADR-025](ADR-025-temporal-evaluation-and-calibration-thresholds.md),
[ADR-026](ADR-026-synthetic-only-model-egress.md).
