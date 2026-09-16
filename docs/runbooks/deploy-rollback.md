# Runbook — Deploy & Rollback

> **Status: live, manual-only.** Deploy workflows require `workflow_dispatch`; a push or successful
> CI run never deploys. Cloud jobs remain independently gated by `AZURE_DEPLOY_ENABLED` and
> `VERCEL_DEPLOY_ENABLED`, plus the protected production environment.

## Pipeline (parity)

The reusable workflow defaults to the full gate for normal CI and releases. A manual deploy selects
the stack it is about to ship, avoiding unrelated application work while retaining repository-wide
security and documentation governance:

```
push dev/release       → ci.yml: full backend + frontend + governance/infra gate (no deploy)
manual backend dispatch → backend + governance/infra verify → build-push (GHCR, build-once SHA)
                        → infra (apply only if changed) → stage revision @0%
                        → gated migration → smoke → promote-or-abort
manual frontend dispatch → frontend + governance verify → Vercel build → deploy → smoke
tag v*                 → release.yml: full verify → git-cliff CHANGELOG → GitHub release
```

The full fast/reliable-deploy detail (build-once-promote-many, probes, promote-or-abort, switch
paths) is in [`azure-deploy.md`](azure-deploy.md).

## Secrets posture (no long-lived secrets in GitHub)

- **Azure**: GitHub→Azure **OIDC** (`id-token: write`, `azure/login@v2`, Terraform
  `use_oidc`/`ARM_USE_OIDC`). No client secret stored.
- **Vercel / Supabase**: tokens fetched **short-lived from Infisical** at job/runtime
  through OIDC machine identities, masked, never persisted.
- **App secrets**: fetched at runtime from Infisical by the Container App — never in CI,
  Terraform state, or the image.

See [`infisical-secrets.md`](infisical-secrets.md) for the Infisical project, identity,
and path setup.

## Enabling deploy (one time)

1. Provision Azure + Vercel + Supabase; bootstrap the Terraform state account
   (see [`infra/terraform/README.md`](../../infra/terraform/README.md)) and rename each
   `backend.tf.template` → `backend.tf`.
2. Configure GitHub→Azure OIDC federation; set repo **variables** (not secrets):
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
   `VITE_API_BASE_URL` (HTTPS gateway URL), `FRONTEND_URL`, `INFISICAL_PROJECT_SLUG`,
   `INFISICAL_GITHUB_ACTIONS_IDENTITY_ID`. (The backend image source is public GHCR by default —
   no `AZURE_ACR_NAME` needed unless `acr_enabled = true`; the staged-revision URL is derived at
   deploy time, so no `BACKEND_STAGING_URL`.)
3. Configure the Vercel production project with publishable `VITE_SUPABASE_URL` and
   `VITE_SUPABASE_ANON_KEY`. The workflow explicitly bakes `VITE_DEMO_AUTH_ENABLED=true`; it
   never enables `VITE_AUTH_DEV_BYPASS`, so every public demo persona uses Supabase + `/api/v1/me`.
4. Flip `AZURE_DEPLOY_ENABLED=true` and/or `VERCEL_DEPLOY_ENABLED=true`.

## Deploy verification

- Backend: the `smoke` job hits **`/healthz` + `/readyz`** and runs `pytest -m smoke`
  against the **staged revision** (still at 0% traffic) **before** any traffic shift.
- Promotion shifts 100% traffic to the new revision only after smoke passes; a failed
  smoke/migration **auto-aborts** (the `abort` job deactivates the staged revision and the
  previous revision keeps serving 100%).

## Rollback

**Backend (Azure Container Apps — instant, traffic-based):**

```bash
# List revisions (newest first)
az containerapp revision list -n fraudlens-prod-api -g fraudlens-prod-rg -o table
# Shift 100% traffic back to the previous known-good revision
az containerapp ingress traffic set -n fraudlens-prod-api -g fraudlens-prod-rg \
  --revision-weight <previous-revision>=100
```

Because each deploy creates a new revision and promotes only after smoke, rollback is a
traffic shift (seconds), not a rebuild. If a bad image was promoted, re-point traffic to
the prior revision, then revert the offending commit and let the pipeline redeploy.

**Frontend (Vercel):** `vercel rollback <previous-deployment-url> --token=$VERCEL_TOKEN`
(or promote the previous deployment in the Vercel dashboard).

**Infrastructure (Terraform):** revert the offending IaC commit and re-run the deploy
(`terraform apply` reconciles). Never hand-edit cloud resources out of band — it drifts state.

## Rollback triggers

- Smoke failure (handled automatically — traffic is never promoted).
- Post-promotion error-rate/latency regression, failed `/readyz`, or a security finding.
