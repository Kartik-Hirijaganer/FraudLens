# Role-Based Access Control Hardening

## Phase 1

### What

Make the demo login roles real enough for a portfolio demo by aligning frontend personas with
backend roles and enforcing permissions at API boundaries.

### Why

The current local demo sign-in stores only an email, while the backend dev bypass mints a single
configured role. That makes Auditor and Fraud Analyst appear distinct in the UI while requests can
still run with admin privileges.

### How

1. Add `auditor` as a canonical backend role and seed a synthetic auditor user.
2. Add permission-based backend RBAC helpers for read, ingest, investigation, alert triage, SAR
   review, rule management, and admin-only model/config actions.
3. Gate mutating endpoints server-side by permission, preserving tenant isolation and audited actor
   checks.
4. Persist selected demo role in the frontend session and send it through a dev-only header/query
   path so local bypass claims vary by selected persona.
5. Hide or block admin UI for non-admin sessions as a UX aid while relying on backend 403s as the
   source of truth.
6. Add focused tests proving auditor/read-only and non-admin/admin denial behavior.

### Trade-offs

- The local demo header is intentionally dev-only and ignored when `auth_dev_bypass` is disabled or
  production mode is active. Real production auth still comes from verified JWT claims.
- Gateway `required_role` remains coarse in v1; API dependencies are the authoritative enforcement
  until method-aware edge policy is introduced.
