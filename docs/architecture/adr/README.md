# Architecture Decision Records (ADRs)

Standalone ADRs for FraudLens — one file per decision, indexed below. **ADR-001 through
ADR-016 predate this directory**: their decision summaries are retained in this index after the
retired master plan that originally held their inline records was removed. ADR-017 and later have
standalone canonical records named `ADR-NNN-<kebab-title>.md`. Each one states its own context,
decision, accepted tradeoffs, and reconsideration criteria, and is readable without opening
another document.

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
| ADR-020 | vLLM + 4-bit AWQ: controlled self-hosted SAR benchmark | Accepted (2026-09-14), superseded on quality by ADR-030 (2026-09-18) | [ADR-020-vllm-awq-self-hosted-sar-inference.md](ADR-020-vllm-awq-self-hosted-sar-inference.md) |
| ADR-021 | AKS: ephemeral Kubernetes demonstration runtime; kind supplies release 0.3 evidence | Accepted (2026-09-14), amended (2026-09-16) | [ADR-021-aks-ephemeral-kubernetes-demonstration.md](ADR-021-aks-ephemeral-kubernetes-demonstration.md) |
| ADR-022 | Source files: absolute 500-line cap with responsibility-based module splits | Accepted (2026-09-13) | [ADR-022-source-file-cap-and-module-splitting.md](ADR-022-source-file-cap-and-module-splitting.md) |
| ADR-023 | SAR quality and privacy gates are deterministic CI contracts | Accepted (2026-09-13) | [ADR-023-sar-quality-and-privacy-gates.md](ADR-023-sar-quality-and-privacy-gates.md) |
| ADR-024 | Agent skills: one canonical source with a generated Codex mirror | Accepted (2026-09-13) | [ADR-024-agent-skills-single-source.md](ADR-024-agent-skills-single-source.md) |
| ADR-025 | Temporal evaluation and calibration-derived risk thresholds | Accepted (2026-09-13) | [ADR-025-temporal-evaluation-and-calibration-thresholds.md](ADR-025-temporal-evaluation-and-calibration-thresholds.md) |
| ADR-026 | Model egress is synthetic-only and derived from persisted provenance | Accepted (2026-09-13) | [ADR-026-synthetic-only-model-egress.md](ADR-026-synthetic-only-model-egress.md) |
| ADR-027 | Durable investigation execution uses leases, fencing, and bounded replay | Accepted (2026-09-14) | [ADR-027-durable-investigation-execution.md](ADR-027-durable-investigation-execution.md) |
| ADR-028 | Paid experiments use measured admission gates and verified teardown | Accepted (2026-09-14) | [ADR-028-paid-experiment-governance.md](ADR-028-paid-experiment-governance.md) |
| ADR-029 | Recurring operational budget: hard caps bound the permanent URL; AKS stays ephemeral | Accepted (2026-09-16), amended (2026-09-18) | [ADR-029-recurring-operational-budget.md](ADR-029-recurring-operational-budget.md) |
| ADR-030 | SAR drafting is a quality-gated model cascade that fails explicitly | Accepted (2026-09-18) | [ADR-030-quality-gated-sar-model-cascade.md](ADR-030-quality-gated-sar-model-cascade.md) |
