# ADR-030 — SAR drafting is a quality-gated model cascade that fails explicitly

- **Status:** Accepted
- **Date:** 2026-09-18
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  `plans/2026-09-16-sar-quality-gate-cascade-and-gated-benchmark.md`;
  [ADR-020](ADR-020-vllm-awq-self-hosted-sar-inference.md) (the raw AWQ decision this supersedes on
  quality), [ADR-023](ADR-023-sar-quality-and-privacy-gates.md) (the deterministic gate this
  promotes to the production path), [ADR-026](ADR-026-synthetic-only-model-egress.md),
  [ADR-028](ADR-028-paid-experiment-governance.md)

## Context

Before release 0.5.0 the deterministic SAR review checks (`agents/checks.py`,
`evaluate_draft_checks`) were reachable only from the multi-agent graph, the offline benchmark, and
tests. The default single-writer path called `parse_and_ground`, which **silently deleted fabricated
citation IDs** and returned a draft that looked clean. An analyst could not tell a draft that cited
correctly from one whose invented citations had been quietly removed, and `SarQualityStatus` was
hardcoded to `EVALUATED` with no evaluator behind it.

The 0.5.0 live matrix measured what that hid. On 1,000 IBM-derived synthetic cases at concurrency
32, raw AWQ terminally failed 91 cases, **85 of them citation fabrications**; BF16 fabricated none.
Quantization did not degrade fluency in a way a reader would notice — it degraded *grounding*, which
is the only property a SAR is worth anything for. Silent repair is the worst possible response to
that, because it converts a detectable failure into an undetectable one.

ADR-020 chose AWQ on memory, latency, and throughput. It was right about all three and silent about
this. Its reconsideration clause is now met.

## Decision

**One inference path, gated before anything is persisted or streamed.** Every SAR draft — single
model or cascade — is judged by the shipped deterministic `SarQualityGate` before it can be served.
A rejected draft is never repaired, never partially emitted, and never reaches the analyst surface.

**The gate is deterministic, not an LLM judge.** It is set membership, schema validity, and
asserted-fact matching against a closed evidence catalog. A model judging a model would need its own
evidence that it judges correctly, and would price every draft twice.

**Quality escalation is separate from transport fallback.** `LlmClient.fallbacks` handles a provider
that is *unreachable*; a cascade stage handles a draft that is *wrong*. They are different failures
with different correct responses, so no cascade stage carries `fallbacks` — a governed transport
fallback may not leave the self-hosted provider, and quality escalation is the cascade's job.

**One attempt per tier, in declared order.** A stage that fails the gate escalates; it does not
retry itself. Retrying the same model on the same input at temperature 0 produces the same draft.

**Explicit failure over degradation.** A cascade that exhausts every tier fails with its reason
codes. The analyst gets no draft rather than a plausible one, and `SarQualityStatus` becomes a real
tri-state (`not_run` / `passed` / `failed`) driven by an evaluator that actually ran.

**Profiles are the routing unit, in production config.** `config/llm/sar-vllm.yml` declares ordered
profiles; a one-stage profile behaves exactly as the pre-0.5.0 single-model path did. The benchmark
measures those same profiles, so it cannot measure something the product does not do.

**Constrained decoding is configured, measured, and — for now — not shipped.** A closed
citation-ID enum makes fabrication structurally impossible, which is strictly better than catching
it. The live canary measured the cost: the exact fact-object grammar passed quality 8/8 and produced
a **441.6-second p95 at concurrency 32**. It is retained as the `awq-bf16` profile and excluded from
the published matrix. The shipped contract is a compact prose envelope the backend hydrates, judged
by the identical gate.

**The external tier ships configured and unmeasured.** `awq-bf16-external` exists, requires
`requires_egress_class: synthetic`, and routes through OpenRouter under zero-data-retention. No
0.5.0 run exercised it, so the published report carries no ZDR eligibility snapshot and claims no
tier-3 behaviour. The tier-3 pilot that would select between `claude-sonnet-4.6` and `gpt-5-mini` is
**not run**; the model is therefore **not frozen**, and nothing in the release depends on it.

### Carried decisions this record closes

| Item | Decision |
| --- | --- |
| **Surface `escalationTier` to analysts?** | **No, not in 0.5.0.** The field is persisted on the draft and derivable from `sar_generation_attempts`, so nothing is lost. Showing "this draft came from tier 2" invites an analyst to weigh a draft by which model wrote it — but the gate has already decided that question, and both tiers' accepted drafts measured reference validity 1.0. It is an audit fact, not a review input. Revisit if a reviewer is ever asked to apply different scrutiny by tier. |
| **`awq-bf16-unconstrained` disposition** | **Shipped production profile, not benchmark-only.** It is the configuration the published evidence actually measured (99.7% served, 9.2% escalated), and marking the measured profile benchmark-only would leave the product running a shape nothing measured. `awq-bf16` (constrained) stays declared and unshipped pending a latency fix. |
| **RunPod REST v1 vs v2** | **Stay on v1 for release 0.5.0.** Three `/v1` breakages landed mid-release and each is patched; 0.5.0 provisions nothing further, so migrating now would ship an unverifiable rewrite of a paid-provisioning path. v2 also deletes the host-quality floors (`minRAMPerGPU`, `minVCPUPerGPU`, `minDownloadMbps`) that let the operator refuse an underpowered host before renting it — the floors AD-4.2's equal-hardware comparison rests on — so the migration needs post-create verification to replace them, not a literal translation. Recorded as an admission precondition for the next paid session, gated behind an owner-approved single-pod smoke. Full inventory in the [Phase 4 handoff](../../handoff/0.5.0-phase4-live-run.md). |
| **Volume encryption on the benchmark Pod** | **Judged exception.** The frozen contract required an encrypted Pod volume and its verification; mid-release the provider rejected the create field, then stopped reporting encryption state at all. Accepted for these sessions because the corpus is the public IBM AML synthetic dataset and contains no PHI. The observed state is recorded on each ledger row, the published report discloses it, and `VLLM_API_KEY` — written to an unencrypted volume at `/workspace/.fraudlens/vllm-api-key` — should be rotated after teardown. |

## Options considered

1. **Keep silent citation repair** — rejected. It converts a detectable grounding failure into an
   undetectable one, which is the opposite of what an audit trail is for.
2. **Gate, but degrade to a warning banner instead of failing** — rejected. A draft the gate rejected
   is a draft whose citations may not support its claims; shipping it with a banner moves the
   burden onto the analyst least able to check it.
3. **LLM-as-judge instead of deterministic checks** — rejected. It needs its own correctness
   evidence, costs a second inference per draft, and is not reproducible across model revisions.
4. **Escalate by retrying the same tier** — rejected. Deterministic decoding makes the retry
   identical, so it buys latency and nothing else.
5. **Ship constrained decoding because fabrication becomes impossible** — rejected on the measured
   441.6-second p95. Structural prevention is better than detection only if the product still
   responds. Retained, not deleted, so the comparison can be repeated when vLLM's guided decoding
   improves.
6. **Escalate straight to a hosted frontier model** — rejected as unmeasured. BF16 resolved **every
   one of the 92** escalated cases; adding a paid third hop before exhausting a free second one
   would buy nothing this release can demonstrate.

## Why this is defensible

The claim rests on measurements, not on the architecture reading well:

| Measured at concurrency 32, 1,000 cases | BF16 alone | AWQ alone | Gated AWQ→BF16 |
| --- | ---: | ---: | ---: |
| Cases served | 99.5% | 90.9% | **99.7%** |
| Citation fabrications | 0 | 85 | **0** |
| Escalated | — | — | 9.2% |
| Case p95 latency | 16,163.9 ms | 16,228.2 ms | 17,184.2 ms (+6.3%) |
| GPU-hours per case | 0.000116 | 0.000113 | 0.000204 (+76.2%, two endpoints) |

The cascade serves more cases than either model alone and fabricates nothing, for 6.3% p95 and 76%
more GPU-time **across two endpoints rather than one**. Every figure is derived by
[`vllm-gated-cascade-benchmark.json`](../../reference/benchmarks/vllm-gated-cascade-benchmark.json)
from the persisted run; the report's `gate_verdict_parity` criterion requires the gate verdict the
production drafter recorded at request time to equal the verdict re-derived from the persisted
output, across all 1,092 attempts, or publication is refused.

## Tradeoffs accepted

- **A gated cascade is slower at the tail than its fast tier, and slower than its slow tier.** An
  escalated case pays both models by construction. That is the cost of not shipping a wrong draft,
  and it is published next to GPU-hours per case so it cannot read as a free win.
- **The cascade's two-endpoint footprint is not a same-resource result.** Every published comparison
  is labelled `same hardware` or `two endpoints vs one` for that reason.
- **AWQ's headline advantage narrows under the production contract.** At c1 AWQ is 49.5% faster than
  BF16; at c32 it is 0.4% *slower*. The memory reduction (63.5%) holds at every level; the latency
  win does not, and the release reports both.
- **Three of 1,000 cases carry no evidence and are refused before any model call.** They count as
  terminal cascade failures rather than being excluded, which is the entire gap between 99.7% and
  100%.
- **Abstention correctness is not published for the cascade.** Measured over accepted drafts it
  would be a vacuous 1.0, and a scenario run has no separate abstention fixtures. The in-population
  evidence is the three refusals.
- **CUDA runtime is absent from the published provenance.** Capture was added after the matrix ran;
  the report says `not captured` rather than inferring a version from the driver.

## Reconsider when

- vLLM guided decoding's latency cost falls far enough that `awq-bf16` clears the same acceptance
  criteria as the unconstrained profile, at which point structural prevention replaces detection.
- A measured tier-1 pass rate rises high enough that escalation is rare enough to make a hosted
  tier-3 affordable, or falls far enough that a two-tier cascade stops paying for itself.
- Accepted-draft reference validity drops below 1.0 on any tier, which would mean the gate is no
  longer sufficient and the evidence catalog needs strengthening rather than the cascade lengthening.
- Real PHI enters scope: `requires_egress_class: synthetic` will then block tier 3 by design, and the
  cascade must be re-reasoned as self-hosted-only rather than silently shortened.
- An analyst workflow emerges that genuinely needs the serving tier surfaced, rather than needing the
  gate verdict that is already surfaced.

A gate that repairs is not a gate. The cascade is only worth its latency because the tier that
served a draft is the tier whose draft actually passed, and because a draft no tier could ground
reaches nobody at all.
