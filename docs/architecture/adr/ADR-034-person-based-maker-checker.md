# ADR-034 — Case decisions require an independent person and an immutable submission

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** [ADR-026](ADR-026-synthetic-only-model-egress.md),
  [ADR-027](ADR-027-durable-investigation-execution.md),
  [ADR-032](ADR-032-upload-validation-and-local-sar-templates.md)

## Context

A Reviewer may investigate as a maker or check another person's work. Role checks alone cannot
establish independence, and comparing account ids fails when one person holds multiple accounts.
The previous combined SAR edit/review API and separate case-close action also allowed approval
without a submission, inline preparation while approving, and mismatched or unfinished outcomes.
Machine enrichment could rewind a case after a human took ownership.

Alternatives considered were account-based separation, an Admin override, retaining compatibility
mutation routes, and storing a second copy of every reason in audit metadata. These leave account
alias bypasses, independent approval/closure transactions, or duplicate free-text records.

## Decision

An independent Reviewer or Admin decides an immutable submitted round, and records its reason,
SAR state, case closure and training outcome in one transaction under the case-row lock.

### Identity and participation

Trusted provisioning creates a tenant-scoped `Person` and an immutable `AccountPerson` binding.
Ordinary application Admin APIs cannot create or remap these links. Unmapped invitations fail
closed for case mutations. Every mutation resolves the persisted account, person and role; HTTP
permissions additionally check the verified token role. Person identity is never a request input.

A case keeps its original owning account and canonical owning person. Actual narrative changes,
manual authoring, regeneration and submission accumulate preparation participation across versions.
Submission freezes that set. Comments and returns do not add the checker as a preparer. A linked
account or changed role cannot erase participation; Admin has no independence override.

Historical artifacts keep unknown person identity as null. Migration 0016 does not fabricate old
submissions, decisions, reasons or identity links. PostgreSQL prevents binding updates/deletes and
submission snapshot updates; the artifact readiness field remains independently mutable.

### Commands, flags and atomicity

Pickup is self-assignment from an unowned open/pending case. Only the owning person prepares its
working narrative. Submission requires a nonblank rationale and stable persisted machine evidence;
it locks the case at `escalated`. A SAR recommendation identifies its exact usable version. A
no-SAR recommendation can be returned or declined, including when machine drafting failed.

Approval requires a submitted usable SAR and confirmed-fraud/false-negative outcome. Decline uses a
benign/false-positive outcome and closes without an approved SAR. Return preserves the frozen round,
restores the original owner and creates a working revision where an artifact exists. Every later
submission gets a different round. Maker dismissal requires a rationale, stable evidence and no
current flags, writes a false-positive label and retains the latest draft as withdrawn.

Mandatory flags derive from persisted scoring evidence and the latest working SAR. Recovery may
clear SAR unavailability; it cannot clear critical risk or low confidence that still exists.
Workflow, regeneration and enrichment acquire the same tenant-scoped case lock. Expected case/SAR
versions reject stale writes. Submission/decision keys are hashed, bound to the exact account,
operation and request fingerprint, and replay a durable serialized result before stale-state checks.

Reasons live once in masked action notes. Audit metadata contains identifiers, transitions and
masking counts. Terminal decisions, reasons, SAR lifecycle and training labels commit together.

### API cutover and artifacts

The combined SAR review route and separate resolve/escalate mutations are removed. The case
workspace uses versioned narrative save, immutable submission and independent decision endpoints.
Investigation shows machine evidence and reuses the same capability-controlled case narrative
editor for inline working-draft changes. Submission and independent decisions remain in the case
workspace. Submitted and decided reports stay read only.

PDF generation runs after approval commits, uses the frozen approved version, and persists truthful
pending/ready/failed readiness. Tenant-authorized download hides storage URIs; Reviewer/Admin retry
is idempotent and can recover a task interrupted after commit. The existing lightweight renderer
now paginates the complete narrative and citation list; its existing Latin-1 font behavior remains.

### Evidence

The contract is held by [portable workflow tests](../../../tests/integration/test_case_workflow.py),
[guard tests](../../../tests/integration/test_case_workflow_guards.py),
[PostgreSQL races and historical migration tests](../../../tests/integration/test_case_workflow_postgres.py),
and [artifact recovery tests](../../../tests/integration/test_alerts_api_sar_review.py).
The [local UAT runbook](../../runbooks/maker-checker-uat.md) documents reproducible operation;
[the evidence report](../../reference/maker-checker-evidence.md) distinguishes measured verification.

## Tradeoffs accepted

- Row locks span regeneration to serialize generation against submission. This favors correctness;
  a future generation reservation can shorten transactions if measured contention warrants it.
- Trusted provisioning is an operator responsibility. An invitation alone does not grant case access.
- Accumulated case participation is conservative across revisions, making independence auditable.
- The coordinated API cutover requires frontend and backend to ship together. No bypassing adapter
  remains. Historical `reviewed` artifacts remain readable but that status is no longer written.
- Deferred PDF work can be lost during process termination; persisted pending state and explicit
  retry make recovery visible. A durable artifact outbox is a future reliability extension.
- Local synthetic reset preserves identities, audit logs, models and foundational configuration.
  Production deployment, production reset, filing and paid cloud operations remain outside this work.

## Reconsider when

- Cross-tenant personnel identities become a requirement: review the identity trust boundary before
  sharing mappings or participation across agencies.
- Preparation collaboration or ownership transfer is introduced: specify how contributions and
  original-maker restoration carry through it before adding reassignment.
- Measured lock contention or artifact recovery frequency warrants generation reservations or a
  durable PDF queue.
- International PDF font requirements exceed the retained renderer's character support.
