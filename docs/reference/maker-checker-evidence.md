# Maker–checker implementation and verification evidence

- **Date:** 2026-10-25
- **Status:** original local implementation verified; release extraction verification is recorded below.
- **Scope:** local implementation and synthetic UAT, including four demo logins and inline SAR
  editing. Production deployment, production reset, regulatory filing and paid cloud actions are
  outside the authorized scope.

## Release extraction — 2026-10-28 UTC

The maker–checker implementation is extracted onto `codex/maker-checker-workflow`, based on
`release/0.7.0` at `origin/main` commit `aefa3f557d2cf0159acb279378797d47d9055b3d`.
Risk-ranking studies, score-explanation changes and semantic regulatory retrieval are excluded.
The original staged implementation remains unchanged in its original checkout.

Release verification is performed independently in the extracted checkout. The implementation-session
counts and browser evidence below are historical evidence, not results from the extracted branch.

| Extraction check | Result |
|---|---|
| `make pre-pr` | Passed the complete canonical gate, including generated documentation, quality and manifest checks. |
| Portable backend suite | 2,366 passed; 25 skipped; 25 deselected; 96.28% branch-aware coverage. |
| Frontend suite | 481 passed; 96.87% statements, 90.90% branches, 97.40% lines. |
| Changed-line coverage vs `release/0.7.0` | Backend 96%; frontend 92%; both exceed the 90% gate, including newly added files. |
| Fresh migrated PostgreSQL | Canonical target: 13 passed, including eight maker–checker proofs. |
| Fresh synthetic baseline | Bootstrap and verification passed: 20 transactions, 15 completed/scored runs, five cases and two labels. |
| Browser → API → PostgreSQL | Keyboard pickup, narrative save/reload, submission and independent approval passed; case version 5, one frozen round, one decision, one label, distinct maker/checker persons. |
| Approved artifact | Persisted readiness `ready`; authorized Auditor download returned HTTP 200 and a 3,253-byte PDF. |
| Responsive UI | At 400 pixels, document and body widths both equal 400; desktop and narrow case layouts inspected. Shared tokens, labeled controls and server-owned capabilities retained. |
| Preservation and teardown | Original staged diff hash remains `77501eea3fa6313d1de99ccea6047eb1b0ff6903` (171 staged files, no unstaged/untracked files). Extraction-only database container and both local listeners removed; original UAT services preserved. |

Extraction logs, the approved PDF, screenshot and result summary are gitignored under
`.local/verification/maker-checker-release-070/` in the extracted checkout. The synthetic database
used PostgreSQL loopback port 55442, API 18101 and frontend 15181, separate from the original session.
No production deployment or reset was performed. Release cutover prerequisites are recorded in the
[UAT runbook](../runbooks/maker-checker-uat.md#release-cutover-prerequisites).

## Original implementation verification — 2026-10-25

### 1. Executive Summary

**Aligned.** All six phases and all twelve acceptance scenarios were verified against the working
plan `docs/handoff/maker-checker-workflow.md`, amended by the user's requests to remove the extra
accounts, add inline editing and remove the duplicate preparation shortcut. That completed plan is
retired after this audit; the [ADR](../architecture/adr/ADR-034-person-based-maker-checker.md),
[runbook](../runbooks/maker-checker-uat.md) and behavioral tests retain the contract.

- Branch: `codex/sar-approval-state`; base HEAD: `25dc067` (unchanged during implementation).
- Trusted provisioning remains an operational prerequisite; unmapped accounts fail closed.
- Regeneration holds a case-row lock while drafting; measured contention may justify a reservation.
- Deferred PDF jobs can be interrupted; persisted readiness and authorized Retry provide recovery.
- Blocking release issues observed in the authorized local scope: none.

| Check | Result |
|---|---|
| `make pre-pr` | Passed the complete canonical gate. |
| Portable backend suite | 2,364 passed; 25 skipped; 25 deselected; 96.27% branch-aware coverage. |
| PostgreSQL canonical target | 13 passed, including eight maker–checker race, immutability, dismissal and migration proofs. |
| Backend changed-line coverage | 96%, including untracked implementation files. |
| Frontend after inline editing | 475 passed; 96.86% statements, 90.71% branches, 97.40% lines. |
| Frontend changed-line coverage | 91%; passed the 90% gate after the inline addition. |
| Fresh PostgreSQL schema | One head `0016_maker_checker`; Alembic: no new upgrade operations detected. |
| Portable migrations | 11 passed, including upgrade/downgrade, parity and one head. |
| Generated documentation | OpenAPI, ERD, architecture/header regions regenerated; 12 skill mirrors remain byte-identical. |
| Source checks | Lint, typechecking, formatting, headers, links, 500-line cap and duplicate scan passed. |
| Dead-code sweep | Advisory sweep; eight standing public-interface type exports, zero confirmed dead items. |

The full gate preceded the inline UI follow-up. Frontend CI/coverage and changed-line coverage were
repeated after that change; backend workflow behavior was unchanged. Documentation and cross-cutting
checks were repeated on the final sources.

| Phase | Verified contract and evidence |
|---|---|
| 1 | Trusted tenant identities, immutable links, frozen rounds, accumulated participation, masked actions; `resolve_actor`, `provision_person_link`, migration 0016 and historical round-trip tests. |
| 2 | Contextual pickup/preparation/comment/submit/check/dismiss policy, authoritative flags and delayed-writer guards; `CasePolicy`, `CaseContext`, `AlertWorkflowService`. |
| 3 | Coordinated API cutover, version fences, receipts, shared locks, atomic outcomes, version-bound complete PDFs and recovery. |
| 4 | Queues, preparation/checking/history, inline Investigation editor, server-owned capabilities, unsaved-change guard, keyboard and 400-pixel behavior. |
| 5 | Four original demo accounts only; workflow-based bootstrap/reset; unsafe routes, helpers, request variants and picker/transport extras removed. |
| 6 | Behavioral suites, PostgreSQL migration/races, browser/API/database walkthroughs, runbook, verified demo baseline and this audit. |

### 2. Drift Analysis

None observed after the user-authorized scope amendments. Stale source comments about the removed
approval shortcut were corrected outside the read-only audit, then checked again. The demo retains
only Analyst, Reviewer, Admin and Auditor; the existing Admin can check Reviewer-prepared work.

### 3. Gap Analysis

None observed. The twelve acceptance scenarios map to executed behavioral assertions:

| Scenario | Evidence |
|---|---|
| Analyst prepares; independent Reviewer approves | `test_independent_approval_is_atomic_and_frozen`; browser/API/SQL walkthrough: frozen SAR 2, resolved, confirmed-fraud label, one decision and one label, ready PDF. |
| Reviewer prepares; independent Admin decides | `test_person_independence_including_linked_admin_and_role_changes`; browser regeneration, submission, return and independent decline. |
| Alias, changed role or Admin self-decision | Same portable test plus `test_aliases_map_once_and_cross_tenant_links_or_remaps_are_refused`; actual Reviewer self-approve/return/decline requests all returned 403 with an unchanged case projection. Aliases are test-only fixtures. |
| Comments/return do not prepare a SAR | `test_return_revise_resubmit_preserves_rounds_and_checker_eligibility`; the returning Admin decided the next browser-created round. |
| Return/revise/resubmit | Same test; browser/API/SQL captured two frozen rounds, preserved original narrative and one terminal label. |
| Decline | Terminal no-SAR browser round stored the reason and benign label; returned/declined PDF requests returned 404. |
| Maker dismissal | `test_manual_recovery_clears_only_current_sar_flag_then_dismisses` and PostgreSQL `test_maker_dismissal_persists_postgres_label_source`. |
| Current mandatory flags | `test_current_flags_block_dismissal_even_without_sar` and `test_current_flags_remain_after_manual_recovery_of_critical_case`. |
| Required reasons/stale/retries | `test_idempotency_stale_and_missing_reasons_leave_no_partial_writes`; browser stale decision showed reload guidance; API 409 and SQL confirmed zero decisions/labels. |
| Locks/tenant/alternate paths | PostgreSQL pickup/edit/regen/decision races; `test_working_drafts_lock_and_old_mutation_paths_are_absent`; retained cross-tenant security tests. |
| PDF failure/recovery | `test_pdf_failure_is_durable_and_authorized_retry_recovers_complete_artifact`; browser Retry and auditor download returned ready/200 with one preserved decision/label. |
| Late machine enrichment | `test_late_machine_writers_preserve_human_state_and_narrative` across owned, submitted and closed cases. |

Additional inline-editor proof: the Analyst took a disposable synthetic case, clicked **Edit SAR**,
typed and saved, and reloaded the Investigation. API and tenant-scoped SQL confirmed case version 3,
SAR version 2, `in_review`, one preparation contributor and no outcome label. Cancel, unsaved wizard
navigation, failed saves, in-flight disabling, permission failures and read-only reports are covered
by [inline editor tests](../../frontend/src/pages/InvestigationSar.test.tsx) and
[Investigation integration tests](../../frontend/src/pages/Investigation.sar.test.tsx).
The actual approved example remained locked and exposed no Edit control. The disposable case was
removed by exact tenant/id scope; original cases and audit history were preserved.

Browser checks included keyboard activation, focus on entering the editor, menu Escape/Enter,
reload persistence and 400-pixel layouts. Document/body width equaled 400 with no horizontal overflow.
The duplicate **Continue preparation in case** button had zero matches. A two-page approved PDF was
rendered and visually inspected; the artifact test also verifies a complete eight-page export with
final narrative/citation sentinels.

Local command logs, screenshots, API responses and SQL assertions are gitignored under
`.local/verification/maker-checker-*`. They include `uat-submitted`, `uat-approved`, `uat-db-approved`,
`uat-self-denials`, `uat-rounds`, `uat-db-rounds`, `uat-stale`, `uat-db-stale`, `uat-pdf-recovery`,
`uat-db-pdf-recovery`, `inline-uat-api`, `inline-uat-db` and `inline-cleanup` evidence. Screenshots alone
were never used as workflow proof.

### 4. Redundancy & Duplication

None observed in the implemented workflow. Searches across runtime code and OpenAPI found no old
`/sar/review` route, separate resolve/escalate command, `next_alert_status`, `set_review_status`,
combined review client or direct Investigation approval handler. The inline Investigation surface
reuses `CasePreparation` and the existing versioned save command. Four-persona selection continues
through the original role-based local transport; extra account/group/selector transport code is gone.
No banned naming additions were found in the scoped implementation. The duplicate scanner
reported the same nine standing repository clones as the original pre-PR run, within its configured
threshold; none is a newly duplicated case-workflow implementation.

### 5. Data Model & Schema Validation

[Models](../../backend/src/fraudlens_backend/db/models/case_workflow.py) and
[migration 0016](../../alembic/versions/0016_add_maker_checker_workflow.py) agree on tenant-scoped
persons, account links, contributors, submissions, decisions and receipts, with required indexes and
composite tenant foreign keys. PostgreSQL rejects link remaps and submitted-snapshot updates while
allowing artifact-readiness updates. Nullable historical identities remain unknown; migration tests
preserve historical reasons and narratives without inventing rounds or mappings.

Schema comparison reports no upgrade operations; one Alembic head exists. Migration 0016 also widens
label source to fit `analyst_dismiss`; explicit enum widths preserve existing provenance/quality
column contracts. [Generated ERD](generated/erd/erd.mmd) and API references are fresh. RLS changes were
not part of this contract; server queries and jobs enforce the existing `agency_id` boundary.

The prepared and verified baseline uses four users/persons/account links, five cases (one open, one in review, one
submitted, two resolved), four submissions, three decisions and two labels. The approved baseline's
PDF is ready. Model/scoring expectations remain unchanged: 20 transactions, 15 scored runs and all
pinned risk bands/rule hits passed. Current local services use PostgreSQL loopback port 55441, API
18100 and frontend 15180; reproduction and teardown are in the runbook.

A later read-only baseline check during interactive testing observed 17 completed/scored runs,
eight low-risk transactions and three unscored transactions. Its seven fixed-baseline distribution
checks correctly failed against the original 15/6/5 calibration; case/action/label counts still
matched. The initial fresh-baseline verification is retained in `maker-checker-final-baseline.log`.
The later result is in `maker-checker-delivery-baseline.log`. Interactive data was preserved rather
than reset; this does not change the passing workflow or scoring-contract tests.

### 6. API Contract Validation

[Business routes](../../backend/src/fraudlens_backend/api/v1/alerts.py) remain under `/api/v1/`:
versioned narrative PATCH, idempotent submission POST, independent decision POST and authorized
[PDF access/retry](../../backend/src/fraudlens_backend/api/v1/submission_pdf.py). Requests use bounded
Pydantic camelCase models, reject unknown/inline-edit decision fields, validate terminal outcomes and
require reasons. Every mutation re-resolves tenant-scoped persisted identity/role and the case lock.

`CaseContext` recomputes flags from persisted evidence; receipt replay occurs under the common lock
before optimistic checks. The API commits domain decisions before deferred artifact work. Safe
errors retain `{code, message, details, requestId}` without raw input or stack traces. Reasons live
once in masked actions; audits contain identifiers, transitions and masking counts.
[Generated API documentation](generated/api/endpoints.md) matches client contracts. Local persona
headers never establish a person identity and the bypass remains inert in production. The inline UI
adds no endpoint or alternate approval path.

### 7. Dead Code & Cleanup Candidates

Zero confirmed dead-code items. The advisory sweep's eight type exports remain used in public
interfaces/signatures: `AgencyStyle` (`agencyStyle`), `StudyMotifNode`/`StudyMotifEdge` (`StudyMotif`),
`LayoutNode`/`LayoutInput`/`GraphLayout` (`layoutGraph`), `PortfolioDemoPersonas`
(`usePortfolioDemoPersonas`) and `SarEvalStudyProps` (`SarEvalStudy`). FastAPI routers and the inline
editor have live registrations/callers. Historical enum readers remain intentionally read-only.

### 8. Risk Register

| # | Description | Affected Components | Severity (L/M/H/Critical) | Recommended Action (advisory only - no code) |
|---|---|---|---|---|
| 1 | Regeneration holds the row lock across drafting. | SAR regeneration/case commands | M | Measure contention before introducing a generation reservation. |
| 2 | A process stop can lose deferred PDF work. | Approved artifact task | M | Use persisted pending/failed state and Retry; consider a durable outbox if measured recovery warrants it. |
| 3 | The retained standard-font renderer has Latin-1 character limits. | PDF export | L | Introduce embedded international fonts when required; complete pagination is already verified. |

AUDIT VERDICT: Aligned — 0 drift, 0 gaps, 0 duplications, 0 dead-code items, 3 risks (0 critical).
