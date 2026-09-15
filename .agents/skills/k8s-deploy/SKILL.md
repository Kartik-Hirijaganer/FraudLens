---
name: k8s-deploy
description: Validate and operate FraudLens Kubernetes flows on local kind or Azure AKS with context checks, Golden-Rule-7 permission gates, autoscaling evidence, durability checks, and cleanup verification.
---

# Kubernetes Deploy

Keep the free local demonstration and the billable AKS path explicit and auditable.

## When To Use

Use for Kubernetes manifest validation, the kind demo, HPA/durability evidence, AKS planning,
deployment, smoke testing, stopping, starting, or teardown.

## Rules

- Identify the active kubectl context and requested platform before any mutation.
- Local `kind-fraudlens-demo` actions are free and exempt from the cloud-permission gate.
- Every AKS, non-kind kubectl, Helm, registry, secret-sync, or cloud mutation requires explicit human
  permission under Golden Rule 7.
- Keep secret values in Infisical `prod`; logs and evidence contain key names/status only.
- Do not equate kind evidence with an actual AKS deployment.

## Steps

1. Read the applicable deployment phase under `plans/` and run `make k8s-tools-check`,
   `make k8s-validate`, `make k8s-demo-test`, and the committed evidence validator.
2. For kind, create/load/deploy through `make kind-up`, `make kind-load`, and
   `make kind-deploy`; confirm the context is `kind-fraudlens-demo` before kubectl writes.
3. Run `make kind-smoke`, then `make kind-hpa-demo` with the worker-kill durability scenario.
   Validate and preserve evidence before `make kind-down`; confirm no kind containers remain.
4. For AKS, stop after `make aks-plan` unless the human explicitly approves the next mutation.
   Re-check subscription, context, projected hourly cost, image tag, OIDC, Infisical identity, and
   rollback/teardown path at each gated step.
5. If approved in a phase that permits AKS apply, use immutable images, install/sync secrets through
   the governed target, deploy, smoke, capture HPA/durability evidence, then stop or destroy as
   directed and run `make aks-verify-clean` after destruction.

## Verification

- Kustomize rendering, kubeconform, IaC scanning, and manifest contract tests pass.
- `/healthz` and `/readyz` pass through the deployed service without secret values in output.
- HPA evidence captures the expected scale-up/scale-down path and zero lost investigation runs after
  a worker kill.
- Evidence records platform, commit, config, image, resource settings, HPA samples, load summary,
  durability, and limitations.
- Cleanup matches the chosen platform: no kind containers locally, or no scoped AKS/cloud residue
  after an approved destroy.

## Never Do

- Never mutate an unverified kubectl context or treat a context name as sufficient tenant/auth proof.
- Never apply AKS during a validate-only phase, bypass the deploy-enable gate, or use `latest` images.
- Never log Kubernetes Secret contents, Infisical values, JWTs, database URLs, or PHI.
- Never claim “deployed on AKS” from Terraform validation or kind-only evidence.
