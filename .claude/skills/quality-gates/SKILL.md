---
name: quality-gates
description: Run and explain FraudLens quality, citation-grounding, hallucination, model-egress, file-length, Kubernetes, benchmark, full-data, and documentation gates with evidence-backed failure analysis.
---

# Quality Gates

Run the relevant deterministic gates and trace failures to the governed boundary they protect.

## When To Use

Use for `make quality-gates`, release-quality review, SAR grounding or model-egress review, or any
request to run and explain the configured quality checks.

## Rules

- Choose the gates the current change actually requires; do not replace the Makefile targets
  with ad hoc approximations.
- Quality and egress suites run provider-free, keyless, and with network denied.
- Treat threshold failures as product evidence; never loosen a threshold merely to turn CI green.
- Preserve synthetic-only scope and never place raw prompts, responses, PHI, secrets, or forbidden
  bytes in logs or reports.

## Steps

1. Run the relevant targets: `make quality-gates`, `make file-length-check`,
   `make k8s-validate`, `make vllm-bench-validate`, `make fulldata-test`, and
   `make docs-links-check`; skip only targets not yet introduced by the active phase and say so.
2. For a SAR grounding review, trace each material claim to supplied evidence, validate citation ids
   and snippet support, require uncertainty where evidence is incomplete, and prove that analyst
   edits invalidate prior automatic quality status until re-evaluated.
3. For a data-egress review, inventory every model-bound field from request/domain state through
   prompt construction, masking, policy validation, serialization, retries, and fallbacks to the
   transport boundary.
4. Exercise rejection paths and confirm disallowed provenance, unknown fields, bad snippet digests,
   or forbidden tokens prevent the socket write across initial, retry, and fallback paths.
5. Report the failing assertion, configured threshold/policy, observed value, affected boundary, and
   safe remediation direction.

## Verification

- Citation precision/recall, unsupported-claim recall, false-positive ceiling, and published-study
  consistency are recomputed from fixtures rather than trusted from summaries.
- Transport capture proves forbidden input bytes never leave the process; blocked requests produce
  zero provider calls and logs contain neither prompts nor responses.
- Cache partitioning includes tenant, evidence snapshot, prompt, model, and generation settings.
- Structural gates validate line caps, links, manifests, full-data behavior, and benchmark bindings
  through their canonical targets.

## Never Do

- Never call detection proof of anonymization or broaden the system to real PHI.
- Never inspect only prompt-builder output when the contract requires transport-boundary proof.
- Never mark an edited draft evaluated using stale pre-edit checks.
- Never update expected fixtures or policy thresholds before explaining the behavioral change.
