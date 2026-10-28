# Offline risk-ranking evaluation

This runbook compares frozen XGBoost bundles and risk policies on public synthetic IBM data.
It does not train, contact providers, read an application database, change defaults or deploy.
The decision and limitations are in [ADR-035](../architecture/adr/ADR-035-risk-ranking-evidence-and-rule-findings.md).

## Inputs and reproduction

1. Install the existing workspace and full-data dependency group with `make backend-install`.
   Supply the three existing CSVs under `.local/aml_data/`; their filenames and SHA-256 hashes
   are pinned in `config/fulldata.yaml`. The runner never downloads them.
2. Supply the frozen study bundle under `.local/fulldata/artifacts/` from the original study.
   The served HI-Small bundle is already in `data/models/`. Metadata hashes, including Platt
   calibration and risk operating points, are pinned in `config/risk-ranking.yaml`.
3. Run `make risk-study-prepare`. It verifies and installs the
   [archived original replay source](../reference/benchmarks/risk-blend-comparison-source.py.txt)
   at its ignored original location, refusing to overwrite a different local script. Its bounded
   account partitions reconstruct rules and the canonical 19 features, verify source hashes and
   reconcile counts. Existing complete inputs are reused. HI-Small materializes all usable rows
   before the new runner reconstructs its original account-whole allocation.
4. Run `make risk-study-run`. Each cache binds feature/label/fold bytes, source hash, booster hash,
   calibration metadata hash and prediction-cache bytes. A mismatch rebuilds the cache or refuses
   the input. Results go to `.local/ranking-review/evaluation/<run>/report.json`.
5. Run `make risk-study-test`. It exercises policy parity, exact budget and ties, fixed-threshold
   workload, original fold allocation, cache corruption, positive-case rule replay, selection
   ordering, pipeline findings/SAR preservation and original-source admission with at least 90%
   branch coverage. It requires no credentials or network.

For a single comparison, use
`uv run --group fulldata python scripts/evaluate_risk_policy.py --run hi-small-served`.
The `--prepare` flag recreates only the selected input when paired with `--run`.

## Interpretation

- Keep the original [70/30 comparison](../reference/benchmarks/risk-blend-comparison.md) and its
  JSON unchanged. The [review](../reference/benchmarks/risk-ranking-review.md) is a later artifact.
  Its model-only candidate was motivated after that historical holdout had been examined.
- Study selection uses calibration expected recall under uniform boundary ties; an exact tie
  favors the existing default. The selection is saved before holdout metric calculation. No
  Platt parameters or operating points are fitted by this runner.
- Every policy gets exactly `ceil(0.05 * N)` review slots, with label-independent seeded ties.
  Report expected tie recall as well as realized counts. Fixed cutoffs include every boundary
  tie and can exceed the nominal budget.
- A separate sensitivity calculation holds the 70/30 model-risk normalization fixed. Primary
  results use each policy's actual normalization. Changing a blend weight changes both its
  arithmetic contribution and the normalization/clipping anchors.
- The original served split is whole accounts ordered by first appearance, not strictly future
  transactions. The Medium split uses timestamp cohorts. Do not equate these estimands.
- For the served model, also report transactions strictly after its latest actual training
  transaction. This is an out-of-time slice, but those dates already entered inspected aggregate
  results; do not present it as a blind confirmatory trial.
- Canonical rule histories are rebuilt for every holdout-positive case, including equal-time
  exclusion, self-transfer direction and the history cap. Patterns can overlap. Gains/losses are
  transaction labels, not distinct criminal cases or SAR filings.
- Daily top-5% comparisons are descriptive stability checks. Accounts and laundering networks
  correlate transactions; do not assert statistical reliability from naive row counts.
- Raw source and account keys stay ignored. Publish aggregate counts, hashes and rule codes only.

## Before changing the deployed policy

Verify the live active/canary pointer, effective tenant rule definitions, runtime blend and cutoff
through authorized read-only access. Use Infisical `prod` for any required credential injection;
never export credentials to files or logs. The recorded snapshot in the review is dated evidence,
not a permanent assertion about future deployment state.

Specify the target review capacity and case-coverage objective, select policy and thresholds on
separate calibration data, evaluate an appropriate unexamined final period, then test the actual
alert/band/queue/SAR workflow. Model-only can keep rule findings as evidence; adding a mandatory
rule override requires an explicit supported policy. Deployment remains a separate human-gated act.
