# ADR-025 — Temporal evaluation and calibration-derived risk thresholds

- **Status:** Accepted
- **Date:** 2026-09-13
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  `plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md` (retired; see [retired-plans.md](../retired-plans.md#retired-plans))

## Context

FraudLens trains on public synthetic IBM AML transactions whose low positive rate and temporal
patterns are materially different from the smaller synthetic fixture. The prior IBM path assigned
whole accounts to folds. That protocol measured generalization to unseen accounts, but it did not
directly measure the production question: how a model trained on earlier activity performs on later
activity. It also derived rare-event risk-band thresholds from final holdout probabilities, allowing
the final-test score distribution to influence an operational model parameter.

The full-data path must process up to 68,228,066 transactions without loading raw CSVs into memory,
survive interruption, and preserve the 19-feature live scoring contract. It operates only on public
synthetic data under `.local/`; it does not receive application database or tenant credentials and
does not alter tenant-scoped serving queries, JWT authorization, or the human-gated promotion flow.

## Decision

FraudLens full-data candidates use strict chronological, whole-equal-timestamp cohorts split 3/5
train, 1/5 middle, and 1/5 holdout; the middle fold is split chronologically into equal tuning and
calibration halves, early stopping sees only tuning, Platt fitting and risk thresholds see only
calibration, and holdout is evaluated exactly once as final test evidence.

Accounts may span temporal folds. Every account key is namespaced by source before local
materialization, and cross-file raw-key overlap is measured in the aggregate report before treating
the three files as independent evidence. The offline DuckDB feature stage uses the same 24-hour
half-open window, equal-time exclusion, most-recent-100 cap, Decimal round-amount rule, and canonical
Python country/channel mappings as live scoring. A sampled parity marker bound to the feature-file
hash is mandatory before training.

The historical `scripts/train_model.py` route adopts the same calibration-only threshold helper.
Model metadata now records `threshold_source`; newly trained bundles write `calibration`, while a
missing field on an older bundle parses as `holdout`, preserving an honest historical label rather
than rewriting provenance.

## Why

**1 · Final-test isolation becomes enforceable.** Tuning controls tree count, calibration controls
probability mapping and operational cutoffs, and holdout supplies only the reported metrics. A
holdout-only signal cannot move a served threshold.

**2 · The evaluation matches future activity.** Chronological cohorts estimate performance on
later transactions. Keeping equal timestamps together prevents arbitrary source-row order from
creating within-instant leakage.

**3 · Training and serving features stay bound.** SQL risk tables are generated from canonical
Python mappings, and the parity stage compares SQL rows to `build_feature_matrix` at 1e-9 tolerance
before any XGBoost fit starts.

**4 · Provenance remains interpretable.** Reports distinguish source, usable, training,
calibration, and evaluation transactions; they do not relabel transactions as SAR cases. Candidate
identity, config hash, source hash, commit, software versions, memory, timing, and cost are retained.

## Options considered and rejected

1. **Keep account-whole folds** — rejected for this study because they answer unseen-account
   generalization rather than future-activity generalization. The earlier protocol remains valid
   historical evidence and is not retroactively relabeled.
2. **Choose thresholds on holdout** — rejected because the threshold becomes a fitted parameter and
   contaminates the final-test role.
3. **Randomly split transactions** — rejected because future rows could influence training while
   earlier rows appear in evaluation, overstating temporal production performance.
4. **Split equal-timestamp rows by source order** — rejected because order inside one timestamp is
   not a defensible causal boundary.
5. **Load each full CSV into pandas** — rejected because peak memory scales with raw-source size and
   prevents bounded laptop/ephemeral-VM execution.
6. **Train one merged cross-source model** — rejected because it hides source-specific prevalence
   and reconciliation, weakens evidence interpretation, and conflicts with the three pre-registered
   candidates.

## Tradeoffs accepted

- Accounts cross folds, so this protocol does not estimate performance on entirely unseen accounts.
  The report names the estimand explicitly.
- Whole timestamp cohorts make realized fold sizes approximate the exact target fractions; manifests
  retain boundaries and actual counts so the deviation is visible.
- Source-account identifiers exist in gitignored intermediate Parquet solely for window grouping and
  overlap measurement. They never enter model features, reports, logs, URLs, or committed artifacts.
- Quantile matrices and the calibration/holdout arrays still consume bounded working memory even
  though CSV parsing and Arrow iteration are streamed; peak RSS is recorded for capacity planning.
- Checkpoint resume can slightly extend wall-clock time and disk use, but avoids restarting long
  Azure runs after spot eviction.

## Reconsider when

- The production objective changes from future-activity detection to cold-start performance for new
  accounts; introduce a separately named account-disjoint protocol rather than silently changing
  this one.
- Event-time precision or source ordering changes enough that minute-level equal-timestamp cohorts
  are too coarse; revise the cohort definition and invalidate prior comparisons explicitly.
- XGBoost or DuckDB changes streaming/checkpoint semantics; re-run checkpoint equivalence and
  feature-parity tests before accepting new published evidence.
- A candidate needs threshold optimization for a different analyst capacity or loss function;
  pre-register the objective and derive it from calibration only.
- Real customer data is proposed for this pipeline; require a separate privacy, tenancy, retention,
  audit, and authorization design before any such use.
