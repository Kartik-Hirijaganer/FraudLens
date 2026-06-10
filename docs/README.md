# Documents

Project documentation and deliverables for FraudLens. Keep documents here (not in the
repo root) — **Golden Rule 4**.

## Layout

| Folder | What goes here |
|--------|----------------|
| `handoff/` | Handoff & onboarding docs, e.g. `AML_Fraud_System_Handoff.docx` |
| `architecture/` | System design, data models, ADRs, diagrams |
| `runbooks/` | Operational procedures, incident response, on-call |
| `reference/` | Specs, regulatory / AML references, external material |

## Conventions

- Prefer Markdown for anything that benefits from review and diffing.
- **Diagrams use [Mermaid](https://mermaid.js.org/)** — author them in fenced ` ```mermaid ` blocks
  (architecture/C4, sequence, ER, flow). They render natively in GitHub/Markdown and diff as text,
  so don't commit binary image exports. The architecture doc's generated ERD and module maps are
  Mermaid too.
- Office / PDF docs (`.docx`, `.xlsx`, `.pptx`, `.pdf`) are tracked as binary (see
  [`.gitattributes`](../.gitattributes)) and are fine to commit here.
- **Never** put secrets, credentials, or raw PHI in documents committed to the repo.
