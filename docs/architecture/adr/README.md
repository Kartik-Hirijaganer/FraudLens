# Architecture Decision Records (ADRs)

Standalone ADRs for FraudLens — one file per decision, indexed below. **ADR-001 through
ADR-016 predate this directory**: they were recorded inline in the master plan
([`plans/2026-06-12-aml-fraud-detection-system.md` §22](../../../plans/2026-06-12-aml-fraud-detection-system.md#22-decision-records-adrs))
and remain canonical there. New decisions land here as `ADR-NNN-<kebab-title>.md`, using the
same format: **Decision · Options · Why · Tradeoffs · Reconsider when**, plus Status and Date.
The master plan's §22 carries a one-paragraph pointer entry for each file-based ADR so both
indexes stay complete.

## Index

| ADR | Decision | Status | Record |
|---|---|---|---|
| ADR-001 | REST + SSE (not GraphQL) | Accepted | [master plan §22](../../../plans/2026-06-12-aml-fraud-detection-system.md#22-decision-records-adrs) |
| ADR-002 | JWT (RS256 via Supabase JWKS) for user auth | Accepted | master plan §22 |
| ADR-003 | LLM: direct + OpenRouter fallback; Azure OpenAI = compliance path | Accepted | master plan §22 |
| ADR-004 | Gateway-first trust boundary | Accepted | master plan §22 |
| ADR-005 | structlog JSON + redaction; audit in Postgres | Accepted | master plan §22 |
| ADR-006 | PHI masking: deterministic-first; Presidio optional | Accepted | master plan §22 |
| ADR-007 | Compute: Azure Container Apps | Accepted | master plan §22 |
| ADR-008 | UI motion: CSS/Tailwind + minimal Framer Motion | Accepted | master plan §22 |
| ADR-009 | Notifications: Sonner toasts + standardized events | Accepted | master plan §22 |
| ADR-010 | Secrets: Infisical (not Azure Key Vault) | Accepted | master plan §22 |
| ADR-011 | Database: Supabase Postgres (not Azure PG) for v1 | Accepted | master plan §22 |
| ADR-012 | Reliability: graceful degradation around a deterministic core | Accepted | master plan §22 |
| ADR-013 | Deployment: build-once immutable image + revision promote-or-abort | Accepted | master plan §22 |
| ADR-014 | PHI storage: masked/hashed only in v1 | Accepted | master plan §22 |
| ADR-015 | Tenant-safe global model training | Accepted | master plan §22 |
| ADR-016 | Run owns execution; SSE is a pure observer/replay | Accepted | master plan §22 |
| ADR-017 | Graph-feature serving boundary: GFP measured offline, never served | Accepted (2026-07-14) | [ADR-017-graph-feature-serving-boundary.md](ADR-017-graph-feature-serving-boundary.md) |
