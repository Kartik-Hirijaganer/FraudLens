# ADR-019 — Multi-agent SAR drafting: bounded enrichment, deterministic control, human authority

- **Status:** Accepted
- **Date:** 2026-08-17
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** [ADR-016 — run owns execution; SSE is an observer](README.md)
  · [ADR-018 — portfolio demo data provenance](ADR-018-portfolio-demo-data-provenance.md)
  · implementation plan
  [`plans/2026-08-17-multi-agent-investigation-and-azure-deployment.md`](../../../plans/2026-08-17-multi-agent-investigation-and-azure-deployment.md)

## Context

FraudLens originally produced each SAR draft with one LLM call behind the `SarDrafter` protocol.
That path preserved a useful deterministic core, but it could not independently investigate the
available evidence, distinguish regulatory interpretation from narrative composition, or review a
draft before it reached the existing human workflow. It also left claim-to-evidence support and
citation correctness concentrated in one generation.

Adding agents creates a larger risk surface, not an automatic improvement. A model-directed
supervisor could expand work without a hard bound; tools could become a route to cross-tenant data,
arbitrary network access, or writes; concurrent nodes could share unsafe database state; and an
agent could be mistaken for a compliance authority. A more expensive workflow also needs evidence
that its quality gain is real. Because FraudLens currently uses synthetic data only, that evidence
can be published, but it must not be represented as validation for real PHI or production filing.

## Decision

FraudLens adds a **bounded four-agent SAR-enrichment implementation** behind the existing
`SarDrafter` seam and feature gates. It changes SAR drafting for an alerted run; it does not change
rules, scoring, SHAP, alert creation, human review, or alert resolution.

- The graph is fixed in code: Evidence Investigator and Regulatory Analyst run in parallel, SAR
  Writer consumes their typed outputs, and Compliance Reviewer either passes the draft or requests
  **at most one** revision. No LLM selects the next node, adds an agent, or extends the loop.
- Deterministic Python checks validate claim evidence references and the closed citation vocabulary
  before the reviewer judges materiality, tone, completeness, and regulatory fit. Citation grounding
  happens only after review so an unsupported citation cannot disappear before the reviewer sees it.
- The Evidence Investigator receives only named, read-only, tenant-scoped tools. Tool schemas contain
  no `agency_id`; the authenticated execution context supplies it. No agent may execute SQL or shell,
  make arbitrary HTTP requests, consume a client-supplied URL, or perform a write. The Writer and
  Reviewer receive no tools. Tool output is fenced and treated as untrusted data.
- Each parallel branch owns a separate database session and writes disjoint graph state. Completed
  node attempts persist in tenant-scoped `agent_executions` rows and resume by replay under a lock on
  `(agency_id, run_id)`; a bare graph thread id is never the tenancy boundary.
- The workflow ends at `SarStatus.DRAFT`. Approval remains exclusively in the existing authenticated
  human review endpoint. No agent path may approve a SAR or resolve or dismiss an alert.
- Cost is denied before the first provider request when the configured worst-case token estimate
  exceeds the per-investigation cap. Agent and workflow timeouts, maximum tool calls, maximum output,
  and one-revision limit bound every live execution. Served model, token, latency, cost, prompt, graph,
  and agent-attempt provenance are persisted for auditability.
- A disabled agency flag, failed preflight, or configured live-agent degradation may fall back to the
  existing **live single-writer** drafter. Live mode never silently falls back to a mock. The original
  single-writer contract and regeneration path remain supported.

The feature is disabled by default in production and additionally requires the tenant-scoped runtime
flag. This is defense in depth for rollout, not a substitute for the tool, tenancy, budget, and human
authority boundaries above.

## Why — specialization is useful only when control stays deterministic

**1 · Separation makes independent review possible.** Evidence retrieval, regulatory analysis,
narrative synthesis, and compliance review have different inputs and failure modes. Typed handoffs
make those boundaries inspectable, while a reviewer that sees deterministic support failures can
challenge a draft instead of trusting the writer's own account of its grounding.

**2 · A fixed graph is the safety and cost boundary.** The useful concurrency is known in advance:
evidence and regulation can be gathered independently. A supervisor model would add latency, cost,
and another prompt-injection decision point without adding a necessary product decision. Code owns
the topology, termination, and revision count.

**3 · `SarDrafter` preserves the governed workflow.** Keeping the existing seam means persistence,
streaming, review, approval, PDF generation, cost rollup, and failure behavior continue to use one
contract. The agent team is an enrichment implementation, not a second investigation pipeline or a
parallel API surface.

**4 · Persisted attempts are evidence, not debugging residue.** Tenant-scoped node records support
restart-safe replay, duplicate-call avoidance, cost attribution, and a human-readable execution
timeline. They also let an audit distinguish a provider response, a refused tool call, a degraded
node, and a revision without placing prompt or transaction content in application logs.

**5 · Publication must be able to report a loss.** The agent path is more complex and costly than the
single writer. It is justified only by a frozen paired evaluation whose headline follows the measured
delta. A neutral or negative result is valid and must remain visible; the benchmark is not allowed to
rewrite its protocol or prose to manufacture a favorable conclusion.

## Options considered and rejected

1. **Keep only the single writer** — rejected as the sole design because it provides no independent
   evidence investigation or pre-human compliance review. It remains the compatibility baseline and
   live fallback.
2. **Use an LLM supervisor to choose agents and termination** — rejected because it makes execution,
   spend, and tool exposure model-directed and nondeterministic. The four roles and their edges are
   known before any request.
3. **Give every agent the same tools** — rejected: synthesis and review do not need data access, and
   broader capabilities would violate least privilege. Writer and Reviewer deliberately have none.
4. **Use a generic LangGraph checkpointer keyed only by `run_id`** — rejected because tenant identity
   would be implicit in an unscoped thread key and persistence would be coupled to framework
   serialization. Explicit tenant-scoped execution rows make replay and audit semantics application
   owned.
5. **Allow the reviewer to approve or transition the alert** — rejected because model review is
   advisory. The established authenticated human transition is the only approval authority.
6. **Evaluate through an internal shortcut** — rejected because it would measure a path that does not
   ship. Both study arms invoke the real API with `workflowMode` and therefore include production
   orchestration and persistence behavior.
7. **Serve evaluation results from a backend endpoint** — rejected because the study is an offline,
   synthetic, committed artifact. A static, lazily loaded page is reproducible and creates no tenant
   query or runtime provider dependency.
8. **Let the writer's model family judge its own output** — rejected because same-family preference
   is an avoidable source of bias. The frozen protocol uses a different judge family, blind labels,
   randomized order, and repeated samples.

## Frozen published-evaluation protocol

The comparison is fixed before inspecting the published result:

- **Cases:** 32 seeded paired scenarios: eight typologies, each with `clean`, `thin_evidence`,
  `conflicting_evidence`, and `citation_bait` variants. All content is synthetic and contains no real
  PHI, credentials, customer data, or production case material.
- **Arms:** the existing live single writer and the bounded multi-agent drafter process the same case
  through the real API using `workflowMode`; neither arm uses a benchmark-only drafting path.
  Requested, resolved-run, and persisted-draft workflow labels must agree. A live fallback is valid
  product behavior, but is rejected from this paired comparison rather than mislabeled as an agent
  result or combined with incomplete cost and call provenance. Explicit workflow selection remains
  admin/evaluation-only. It may bypass public-demo abuse quotas so the frozen matrix is executable,
  but never bypasses tenant daily or per-run budgets. Arm order is seeded and randomized per scenario.
- **Writer/judge separation:** the writer is `openai/gpt-5-mini`; the judge is
  `anthropic/claude-opus-4.6`. Exact served models, prompt versions, and content hashes are published.
- **Frozen lineage:** canonical config bytes determine the run id and are hash-bound through every
  stage. Spending and publication fail closed if the scenario matrix, config bytes, run id, judge
  prompt bytes, or prompt provenance no longer match. Synthetic transaction keys are run-scoped, and
  a same-run idempotent reuse must match the expected visible transaction facts. Completed API arms
  are checkpointed with their original persisted run duration; retrying a failed arm requires an
  explicit fresh attempt identity.
- **Blinding and stability:** the judge receives no arm identity, A/B order is deterministically
  randomized per scenario, and each narrative receives three independent samples. The median score
  and separate inter-sample agreement for FinCEN elements, unsupported-claim counts, and cited spans
  are reported; the composite agreement is the validated mean of those components.
- **Evidence boundary:** each blind sample receives the complete PHI-free scenario input, the exact
  durable scoring, rule, SHAP, model, and regulatory-retrieval facts persisted for the arm, plus the
  deduplicated completed tool results for that scenario only. Tool evidence is shared without an arm
  label so it cannot unblind A/B, and agent-generated interpretations are not promoted to ground
  truth. The paired arms must agree on their durable core facts. A narrative is never judged against
  its own output citations as if they were input evidence, and every reported quote is verified as an
  exact substring of its candidate narrative.
- **Judged metrics:** unsupported claims and narrative completeness across the five required FinCEN
  elements, with pass/fail evidence and a quoted narrative span for each element.
- **Programmatic metrics:** citation precision, recall, and fabricated-id count use the closed ingested
  citation vocabulary. Cost and successful provider-call counts use persisted draft/runtime
  provenance; latency is the persisted investigation `createdAt`-to-`updatedAt` duration. None is
  inferred by the judge.
- **Inference:** paired deltas use a BCa bootstrap 95% confidence interval with 10,000 resamples and a
  fixed seed. `significant` is derived solely from whether the interval excludes zero and is validated
  when the artifact is parsed.
- **Publication:** both generated files are validated and staged before replacement; a failure while
  installing either restores the prior bound pair. The docs report and frontend projection are
  SHA-256 bound, and the free CI validation rechecks the committed pair. The research page is lazily
  loaded, displays a prominent synthetic-offline banner and ADR link, derives its headline from the
  measured delta's sign, and makes no backend request.

Changing cases, arms, prompts, model-family separation, sample count, metrics, bootstrap method, or
headline rule after seeing results invalidates comparison with the published study and requires a new
protocol version with an explicit disclosure.

## Tradeoffs accepted

- **More latency and spend.** Four roles plus a possible revision cost more than one generation. Hard
  preflight and runtime limits bound the exposure but do not make the paths equivalent.
- **An LLM judge remains subjective.** Cross-family judging, blinding, randomized order, three
  samples, quoted spans, and agreement reporting reduce and expose bias; they do not eliminate it.
- **More persisted state and operational paths.** Resume records, locks, feature flags, fallbacks, and
  per-agent telemetry are additional maintenance surface accepted in exchange for auditability and
  restart safety.
- **Fallback reduces feature consistency.** An investigation may produce a single-writer draft when
  the agent path degrades. Provenance and UI workflow labels must make that visible rather than imply
  every draft used four agents.
- **The published evidence is synthetic-only.** It demonstrates behavior on designed scenarios. It
  does not establish clinical suitability, regulatory filing accuracy on real cases, PHI compliance,
  or superiority on an agency's production distribution.

## Reconsider when

- The paired study shows no material quality benefit relative to cost or latency. Keep or restore the
  single writer as the default; do not loosen the protocol to rescue the agent result.
- A proposed role, tool, loop, or autonomous transition cannot fit the fixed capability and human-gate
  boundaries. That requires a new ADR and security review, not reinterpretation of this one.
- Work moves beyond synthetic data. Before any real PHI or production filing is in scope, require a
  dedicated privacy/compliance review, provider-contract review, retention decision, and representative
  evaluation; this ADR supplies none of those authorizations.
- An external durable worker replaces in-process execution. Preserve tenant-scoped idempotency,
  persisted attempt provenance, bounded retries, and the human approval boundary across that move.
- Judge models or provider availability change. Publish a new protocol version and a new bound artifact;
  never silently swap the evaluator underneath an existing result.

**Never** interpret “multi-agent” as authority to add an unbounded supervisor, general-purpose tool,
cross-tenant read, write capability, or model-controlled approval. Those are the boundaries this
decision exists to keep explicit.
