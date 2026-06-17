# Risk Register Remediation

## Phase 1

### Goal

Resolve the six drift-check risk-register items from the all-phase audit of
`plans/2026-06-12-aml-fraud-detection-system.md`.

### Scope

- Add explicit PHI masking/access audit actions.
- Wire production JWT verification through a JWKS-backed verifier.
- Restore the planned config/dev API surface and camelCase path parameter contract.
- Remediate the Python dependency audit failure.
- Fill missing release docs and refresh stale architecture/README content.
- Remove advisory dead-code cleanup candidates.

### Validation

- Backend lint/type/tests plus focused security/API tests.
- Frontend lint/type/coverage.
- `make deps-audit`, `make docs-check`, `make secrets-scan`, `make no-hardcoding-check`,
  `make tenancy-check`, and `make release-gate`.
