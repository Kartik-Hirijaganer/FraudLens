# ADR-018 — Portfolio demo data provenance: pipeline-produced, config-asserted, single-tenant

- **Status:** Accepted
- **Date:** 2026-07-26
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when (per master-plan §22)
- **Related:** [ADR-015 — tenant-safe global model training](../../../plans/2026-06-12-aml-fraud-detection-system.md#22-decision-records-adrs)
  · [ADR-017 — graph-feature serving boundary](ADR-017-graph-feature-serving-boundary.md)
  · implementation plan [`plans/2026-07-25-portfolio-demo-story.md`](../../../plans/2026-07-25-portfolio-demo-story.md)
  · runbooks [`portfolio-demo.md`](../../runbooks/portfolio-demo.md) · [`portfolio-demo-uat.md`](../../runbooks/portfolio-demo-uat.md)

## Context

FraudLens needs a demo a visitor can open and immediately understand: populated risk bands, an alert
queue with realistic states, SARs in review, a named active model, and rows left unscored so the
visitor can start an investigation and watch it run. Before this decision the dashboard opened empty
for a demo visitor, and the causes were structural — the live boot path ran no seed, ingest, model
activation, or scoring; the batch runner's entry point was pinned to one hardcoded tenant; and the
IBM case pack spread rows across three tenants, two of which were never investigated and one of
which had no login at all.

The obvious fix — insert rows into `transactions.risk_band`, `alerts`, and `sar_drafts` so the
screens look full — is available, cheap, and wrong for a system whose entire claim is that its
decisions are produced by a governed pipeline. This ADR records the alternative and the constraints
that make it hold.

## Decision

The portfolio demo's state is **produced by the real pipeline and asserted against configuration**,
in one runtime tenant:

- **Nothing is written directly** into `transactions.risk_band`, `alerts`, or `sar_drafts`. The
  bootstrap ingests authored payloads through `TransactionRepository.ingest` and hands the scored
  subset to the same `run_batch_score` a production job uses; bands, alerts, and SAR drafts are
  whatever the rules → XGBoost → SHAP → blend → alert → SAR chain produces. Alert and SAR states are
  reached through `AlertWorkflowService` — the same transition implementation the interactive API
  calls — never by an UPDATE.
- **Every demo-specific value lives in `config/portfolio-demo.yaml`** and nowhere else: the agency,
  the personas, the pinned model, the authored case pack, the workflow actors and notes, and the
  expected distribution. `scripts/check_no_demo_literals.py` derives its forbidden literals from that
  document and fails CI when any of them is restated in code, tests, docs, or workflows.
- **The declared distribution is an assertion, not a target.** Verification re-reads live state and
  compares it with `expected:`; a delta fails the bootstrap, the deploy step, and
  `make portfolio-demo-verify`. It never adapts the story to what it found.
- **Exactly one persistent runtime demo tenant exists.** Research partitions are an offline analysis
  concept, and generic multi-tenancy is proven by tests that mint temporary tenants.

## Why — a demo that lies is worse than an empty one

**1 · Inserted state falsifies the only claim the demo makes.** A visitor looking at a CRITICAL band
is being told "the model and the rules agreed this is critical." If the row were inserted, that
sentence is false, and every downstream artifact — the SHAP contributions, the SAR narrative, the
audit trail, the alert's `review_flags` — is either absent or fabricated to match. Pipeline
production makes the screens *evidence* rather than *illustration*, and the cost of that is bounded:
authoring 20 payloads that satisfy the live rule parameters.

**2 · Assertion is what makes the configuration honest.** A story that adapts to whatever the
pipeline produced would pass forever and detect nothing. Because `expected:` is compared and not
written, a retuned rule parameter, a different active model, or a changed band bound surfaces as a
named delta — which is the point. `--probe` reports the achieved calibration and prints a paste-ready
block, but **pinning is a human commit**: an auto-heal would convert a regression into a silent
rewrite of the story.

**3 · Recalibration must stay the only remedy.** When the distribution does not match, the two
sanctioned moves are to adjust the authored synthetic inputs and re-probe, or to change the declared
distribution as a reviewed commit. Lowering a band bound, retuning a rule parameter, or writing a
band directly are all out of bounds — each of them makes the assertion vacuous in exactly the way
inserted state does.

**4 · One runtime tenant, because the alternative was buying a demo moment with real risk.** A second
populated tenant would have bought a "sign in as another tenant and watch the data disappear" moment.
It would also have meant a second persistent identity set, a second calibration to maintain, and a
second place for a tenant-scoping bug to hide behind demo data. Isolation is instead proven where
proof belongs — `tests/security/test_temporary_tenant_isolation.py` mints throwaway tenants and drives
them through the repositories, the dashboard aggregate, the uninvestigated-row selector,
`AlertWorkflowService`, and the HTTP surface, asserting tenant-safe 404s. Tests can mint a hostile
tenant a demo cannot.

**5 · Research partitions are not tenants.** The committed GFP tenant-isolation study
([ADR-017](ADR-017-graph-feature-serving-boundary.md)) needs three named partitions to keep its
published artifact reproducible, and its cross-tenant motifs span them. Those names moved to a
study-owned constant (`scripts/lib/gfp/partitions.py`), explicitly named for what they are: offline
analysis partitions. The demo tenant declares the partition it mirrors via
`agency.research_partition_key`, which is also what exempts that one shared string from the
no-duplicated-literals guard — rename the agency without renaming the partition and the guard re-arms.

## Options considered and rejected

1. **Insert demo rows directly into `transactions.risk_band` / `alerts` / `sar_drafts`** — rejected:
   fastest to build, but it fabricates the exact claim the product makes, and leaves no run,
   inference log, SHAP snapshot, or audit trail behind the numbers on screen.
2. **Generate the story from a fixture dump / SQL seed** — rejected for the same reason at one
   remove: a dump is inserted state with a build step, and it silently rots the moment a rule
   parameter, band bound, or model changes, because nothing compares it to anything.
3. **Auto-heal: re-pin `expected:` whenever the pipeline disagrees** — rejected: it makes every
   assertion trivially true. A model regression and a deliberate re-pin become indistinguishable, and
   the demo's numbers stop being reviewable.
4. **Tune thresholds or rule parameters until the desired distribution appears** — rejected: it moves
   *production* policy to satisfy a demo. The authored inputs are data chosen to satisfy the live
   parameters; the parameters are not chosen to satisfy the story.
5. **Two or more populated runtime tenants for a live isolation demo** — rejected: see Why §4. The
   demo moment is real but is paid for in duplicated identity, duplicated calibration, and a larger
   surface for a scoping bug; temporary-tenant tests prove the invariant more strongly.
6. **Keep demo identities as TypeScript/Python constants and skip the config boundary** — rejected:
   the same id then lives in the seed, the dev bypass, the frontend picker, the batch runner, and
   several test suites, and they drift. One validated document plus a derive-from-config CI guard
   costs less than the drift did (it found 51 restatements across 19 files when first run).

## Tradeoffs accepted

- **The live two-login isolation moment is gone.** The research page still renders the cross-tenant
  motif from its committed artifact, and the isolation suite still proves the invariant, but a
  visitor cannot watch data vanish on sign-in.
- **Authoring is harder than inserting.** Each scenario must satisfy the live rule parameters, and
  every history a scored row needs must be another row in the same 20-row story, because history is
  windowed on the masked account. Two rows in the pinned story sit within ~0.03 of the critical
  boundary and will be the first to move under a retune.
- **The demo can fail.** A drifted model, an edited amount, or a retuned rule breaks the bootstrap
  and the deploy step instead of degrading quietly. That is intended: failing pre-promote is cheaper
  than a demo that shows wrong numbers to a reader who trusts them.
- **A visitor mutates state.** Investigating a held-unscored row or resolving an alert is the point,
  and it makes verification report a delta afterwards. The scheduled reset returns the tenant to its
  baseline; the held-unscored rows sit after every scored row precisely so a live investigation
  cannot move a pinned expectation.

## Reconsider when

- A demo requirement genuinely needs a **second persistent tenant** (e.g. a cross-tenant supervisory
  view). That is a new ADR: it must state how the second tenant's calibration is maintained and how
  isolation testing stays independent of it.
- The story grows past the point where hand-authoring against live rule parameters is workable. The
  remedy is a *generator* that still writes through ingest and still asserts its output — never a
  fixture of pre-banded rows.
- The pinned bundle becomes unavailable, or a per-model calibration change makes the operating-point
  map unrepresentative. Re-pin `model.version_label`, recalibrate, and commit the new `expected:`
  block; do not relax the assertion.

**Never** by reinterpreting this ADR: "just this once, insert the row" and "just widen the expected
range" are the two failure modes it exists to prevent.
