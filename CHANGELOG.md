# Changelog

All notable changes to FraudLens. Format follows Conventional Commits + SemVer.
This file is regenerated on release by `git-cliff` (see `cliff.toml` and
`.github/workflows/release.yml`).

## [Unreleased]

## [1.0.0] - 2026-06-15

First complete, locally-demoable release of the AML / fraud-investigation system
(`plans/2026-06-12-aml-fraud-detection-system.md`). One-command local demo via
`make local-demo`; deploy stays gated behind the §20 release gate.

### Features

- Gateway-first trust boundary: a single external edge enforcing JWT authN, `agency_id`
  tenant + RBAC authZ, CORS allowlist, rate limiting, request-id propagation, and security
  headers in front of in-process services.
- Transaction ingestion (single / batch / CSV) with validation, dedup, and deterministic
  PHI masking at ingest (masked-only storage + `feature_hash`).
- Deterministic, versioned AML rules engine with weighted subscores and per-rule fault
  isolation.
- XGBoost scoring + SHAP explainability served via the active model-registry pointer, with
  an LR baseline and quantitative promotion gates.
- RAG over FinCEN/BSA (LangChain + ChromaDB) with grounded citations and a baked index.
- LLM-assisted SAR drafting (mock + live) with prompt versioning, in/out guardrails, PHI
  masking before the prompt, and a budget guard.
- LangGraph investigation orchestration with a POST-owned persisted run and SSE replay.
- Alerts & review workflow (assign / escalate / resolve / dismiss → training labels), SAR
  review → PDF, and an immutable audit trail.
- Model lifecycle (retrain → eval → shadow → human approve → canary → rollback) with
  tenant-safe global training and advisory drift, no redeploy.
- React + Vite + Tailwind frontend (`wise` design system): dashboard, investigation stream,
  model admin, toasts, skeletons/loading/empty/error states, and reduced-motion support.
- Observability (structlog + App Insights, scrubbed), Azure deploy scaffolding (single
  external gateway app; internal split validated-not-applied), and release/maintenance
  automation (git-cliff CHANGELOG, Renovate, the `release-gate` verifier).

### Notes

- No real PHI: synthetic IEEE-CIS data only. Secrets resolve from Infisical at runtime.
- Earlier foundation work (tooling, conventions, CI/CD, IaC scaffolding) shipped under
  `plans/2026-06-09-tech-stack-foundation-and-workflows.md`.
