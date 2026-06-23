# Risk Register Remediation

## Phase 1

### What
- Replace deferred Azure storage/job backend placeholders with operational managed-identity REST clients.
- Wire required Azure resource names into settings and Terraform Container Apps environment variables.
- Make the local job backend execute known local jobs so Model Admin retrain works during local UAT.
- Add an enforceable local release/UAT command path and update docs/tests.

### Why
- The AML implementation audit found production cloud backends and local retrain/UAT workflow as the remaining release risks.
- Local testing should exercise the same operator workflow the plan promises, while production selectors should not fail at runtime.

### How
1. Add typed Azure settings for Blob storage, ARM job start, managed identity, and local job execution.
2. Implement Azure Blob REST `put/get` and Container Apps Job REST `start`.
3. Make `LocalJobBackend.submit("retrain")` run `scripts/retrain.py` synchronously and return a persisted job id when available.
4. Wire Terraform modules with the non-secret environment variables those backends require.
5. Add tests and refresh docs for the local test path.
