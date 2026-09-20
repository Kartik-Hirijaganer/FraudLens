# Documents

Project documentation and deliverables for FraudLens. Keep documents here (not in the
repo root) — **Golden Rule 5**.

## Layout

| Folder | What goes here |
|--------|----------------|
| `handoff/` | Handoff & onboarding docs, e.g. `AML_Fraud_System_Handoff.docx` |
| `architecture/` | System design, data models, ADRs, diagrams |
| `runbooks/` | Operational procedures, incident response, on-call |
| `reference/` | Specs, regulatory / AML references, external material |

## Generated documents

Some documents here are **generated artifacts**: they are committed so their numbers are
reviewable and diffable, but they are produced by a `make` target and must never be hand-edited.

| Document | Regenerate with | Derived from |
|----------|-----------------|--------------|
| [`reference/cost-model.md`](reference/cost-model.md) | `make azure-cost-plan` | The committed Terraform shapes plus live Azure Retail Prices |
| [`reference/benchmarks/summary.md`](reference/benchmarks/summary.md) | `make docs` | The published benchmark artifacts in that directory |
| [`reference/generated/api/`](reference/generated/api/) | `make docs` | The live FastAPI app |
| [`reference/generated/erd/`](reference/generated/erd/) | `make docs` | Live SQLAlchemy metadata |

## Conventions

- Prefer Markdown for anything that benefits from review and diffing.
- **Diagrams use [Mermaid](https://mermaid.js.org/)** — author them in fenced ` ```mermaid ` blocks
  (architecture/C4, sequence, ER, flow). They render natively in GitHub/Markdown and diff as text,
  so don't commit binary image exports. The architecture doc's generated ERD and module maps are
  Mermaid too.
- **Product media is the one carve-out.** The rule above governs *diagrams*. Screenshots and demo
  recordings of the running application cannot be Mermaid, so they are committed as binary under
  [`screenshots/`](screenshots/) and [`demo/`](demo/) and referenced from the README.
- Office / PDF docs (`.docx`, `.xlsx`, `.pptx`, `.pdf`) are tracked as binary (see
  [`.gitattributes`](../.gitattributes)) and are fine to commit here.
- **Never** put secrets, credentials, or raw PHI in documents committed to the repo.
