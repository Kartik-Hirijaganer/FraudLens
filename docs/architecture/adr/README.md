# Architecture Decision Records (ADRs)

Standalone ADRs for FraudLens — one file per decision, indexed below. **ADR-001 through
ADR-016 predate this directory**: their decision summaries are retained in this index after the
retired master plan that originally held their inline records was removed. ADR-017 and later have
standalone canonical records named `ADR-NNN-<kebab-title>.md`, using the same format:
**Decision · Options · Why · Tradeoffs · Reconsider when**, plus Status and Date. The active
implementation plan that introduces a new file-based ADR carries a one-paragraph pointer to its
canonical record.

## Index

| ADR | Decision | Status | Record |
|---|---|---|---|
| ADR-001 | REST + SSE (not GraphQL) | Accepted | Historical summary; original inline plan retired |
| ADR-002 | JWT (RS256 via Supabase JWKS) for user auth | Accepted | Historical summary; original inline plan retired |
| ADR-003 | LLM: direct + OpenRouter fallback; Azure OpenAI = compliance path | Accepted | Historical summary; original inline plan retired |
| ADR-004 | Gateway-first trust boundary | Accepted | Historical summary; original inline plan retired |
| ADR-005 | structlog JSON + redaction; audit in Postgres | Accepted | Historical summary; original inline plan retired |
| ADR-006 | PHI masking: deterministic-first; Presidio optional | Accepted | Historical summary; original inline plan retired |
| ADR-007 | Compute: Azure Container Apps | Accepted | Historical summary; original inline plan retired |
| ADR-008 | UI motion: CSS/Tailwind + minimal Framer Motion | Accepted | Historical summary; original inline plan retired |
| ADR-009 | Notifications: Sonner toasts + standardized events | Accepted | Historical summary; original inline plan retired |
| ADR-010 | Secrets: Infisical (not Azure Key Vault) | Accepted | Historical summary; original inline plan retired |
| ADR-011 | Database: Supabase Postgres (not Azure PG) for v1 | Accepted | Historical summary; original inline plan retired |
| ADR-012 | Reliability: graceful degradation around a deterministic core | Accepted | Historical summary; original inline plan retired |
| ADR-013 | Deployment: build-once immutable image + revision promote-or-abort | Accepted | Historical summary; original inline plan retired |
| ADR-014 | PHI storage: masked/hashed only in v1 | Accepted | Historical summary; original inline plan retired |
| ADR-015 | Tenant-safe global model training | Accepted | Historical summary; original inline plan retired |
| ADR-016 | Run owns execution; SSE is a pure observer/replay | Accepted | Historical summary; original inline plan retired |
| ADR-017 | Graph-feature serving boundary: GFP measured offline, never served | Accepted (2026-07-14) | [ADR-017-graph-feature-serving-boundary.md](ADR-017-graph-feature-serving-boundary.md) |
| ADR-018 | Portfolio demo data provenance: pipeline-produced, config-asserted, single-tenant | Accepted (2026-07-26) | [ADR-018-portfolio-demo-data-provenance.md](ADR-018-portfolio-demo-data-provenance.md) |
| ADR-019 | Multi-agent SAR drafting: bounded enrichment, deterministic control, human authority | Accepted (2026-08-17) | [ADR-019-multi-agent-sar-drafting.md](ADR-019-multi-agent-sar-drafting.md) |
