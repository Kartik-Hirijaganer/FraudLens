# Risk-blend weights at a fixed alert budget

- Date: 2026-10-26
- Status: measured, retrospective offline replay; serving policy unchanged.
- Source: full public synthetic IBM HI-Medium; no application database or provider calls.
- Frozen model: `xgb-ibm-aml-hi-medium-5835992a6919`.
- Evaluation: 6,358,506 held-out transactions, 10,632 laundering labels; exactly 317,926 alerts per policy (top 5%, rounded up).
- Selection: 80/20 selected on the preceding 3,179,474-row calibration fold (3,505 positives) before computing held-out blend metrics.

## Result

**80/20 has the highest observed recall among the four requested blends.** Its held-out recall is 77.7652%, versus 77.3796% for 70/30: 41 additional labelled positives at the same review volume, a 0.3856 percentage-point gain. Raw model-only ranking reaches 77.8969% (14 additional positives over 80/20). The measurements do not justify calling 70/30 optimal.

| Model / rules | Calibration recall, tie-adjusted | Holdout recall, seeded ties | Holdout recall, tie-adjusted | Holdout precision | Positives detected |
|---|---:|---:|---:|---:|---:|
| 50/50 | 44.0304% | 41.0083% | 41.0083% | 1.3714% | 4,360 |
| 60/40 | 61.7941% | 66.1588% | 65.8236% | 2.2125% | 7,034 |
| 70/30 | 73.1241% | 77.3796% | 77.3796% | 2.5877% | 8,227 |
| 80/20 | 73.7518% | 77.7652% | 77.7652% | 2.6006% | 8,268 |

| Control | Holdout recall, seeded ties | Holdout recall, tie-adjusted | Positives detected |
|---|---:|---:|---:|
| raw_model_only | 77.8969% | 77.8969% | 8,282 |
| rules_only | 3.5271% | 3.4727% | 375 |

## Method and checks

1. Use the original chronological calibration and holdout boundaries from the [published temporal study](ibm-full-data-training.md). Verify the pinned input and frozen booster SHA-256 hashes. Reproduce the original fold row counts and positive counts exactly. No retraining or holdout-based threshold fitting occurs.
2. Replay the six canonical default rules using the most recent 100 strictly earlier same-account transactions within 168 hours. Apply each rule's own window, preserve equal-timestamp exclusion and self-transfer direction, and construct the 19 model features using the canonical 24-hour windows. No database overrides are included.
3. For each weight, use its actual `RiskPolicy.model_risk` normalization and convex blend. Rank independently and allocate exactly `ceil(0.05 * N)` alerts. A fixed score threshold such as 0.60 would yield different workloads, so it is not the comparison criterion.
4. Break score ties with seeded independent uniform keys (seed 1729), without labels. Also report expected recall under uniform selection within the boundary tie. Select the highest calibration expected recall, with higher model weight breaking an exact selection tie. The 60/40 held-out cutoff has 151,976 tied transactions; 101,899 of those fit the budget, so its seeded and expected results differ.
5. Check a second comparison with the 70/30 normalization held fixed across all four weights. This separates blend weighting from weight-dependent clipping in the serving normalization.
6. Verify 1,000 randomized rule contexts covering all six rules, 134 additional sampled/first-mask rule comparisons during actual replay, 100 sampled feature rows against the independent live-compatible builder, and 100 vector/scalar normalization comparisons per policy/fold.
7. Maximum sampled feature error: `9.23705556488e-12`, below `1e-9`. Raw-model holdout recall exactly reproduces the published `0.7789691497366441` baseline. Reconcile alert counts, true/false positives, precision, and recall independently before publishing.

## Normalization sensitivity

The serving normalization depends on the weight. For 50/50 and 60/40, the model contribution saturates earlier and loses ranking resolution. Their primary results include that behavior. Holding the model normalization fixed gives:

| Model / rules | Calibration recall | Holdout recall |
|---|---:|---:|
| 50/50 | 69.1298% | 72.9402% |
| 60/40 | 71.5835% | 76.1287% |
| 70/30 | 73.1241% | 77.3796% |
| 80/20 | 73.7518% | 77.8123% |

80/20 remains the best of the requested blends in this sensitivity comparison; model-only ranking still has higher recall.

## Scope and interpretation

- These are transaction-label metrics on one synthetic source and a frozen temporal-study model. They do not establish a universal weight or customer production performance. Transactions are not SAR cases.
- The live portfolio demo pins a different, earlier HI-Small model (`xgb-ibm-aml-fs2-9d43c5f92a`). This study does not validate changing that bundle's serving policy.
- The historical holdout was previously used to publish model metrics. This is a retrospective policy comparison; candidate weights were pre-specified and selected on calibration before their holdout metrics were computed.
- The 80/20 versus 70/30 difference is an observed gain. No cluster-aware confidence interval or cross-source replication was performed, so statistical superiority is not claimed.
- Raw model-only ranking slightly outperforms all tested blends under this objective. This experiment does not measure any separate review value of displaying rule findings.
- Serving weights, thresholds, model pointers, and cloud resources were not changed. Adopting a different policy requires evaluating the relevant serving bundle and its threshold/workload behavior.

## Interview answer

> “70/30 started as a model-led heuristic. I compared 50/50, 60/40, 70/30 and 80/20 using chronological calibration and held-out IBM AML data, with the same 5% alert budget. On 6.36 million held-out transactions, 80/20 captured 77.77% of labelled positives versus 77.38% for 70/30. Model-only ranking reached 77.90%, so I would describe 70/30 as a starting policy rather than an empirically optimal weight.”

## Evidence and reproduction

- [Aggregate metrics, protocol, hashes, and verification](risk-blend-comparison.json).
- The local replay and independent checks are retained under `.local/risk-blend-study/run.py` and `.local/risk-blend-study/checks.py`; their hashes are included in the JSON. Input and intermediate account data stay gitignored.
- Local rerun: `.venv/bin/python .local/risk-blend-study/run.py --candidate hi-medium`, followed by `.venv/bin/python .local/risk-blend-study/checks.py hi-medium`.
- Evaluation-isolation rationale: [ADR-025](../../architecture/adr/ADR-025-temporal-evaluation-and-calibration-thresholds.md).
