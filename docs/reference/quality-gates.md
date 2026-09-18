# SAR quality and model-egress gates

FraudLens runs a named, deterministic Phase 3 contract for citation quality, hallucination detection,
published-study consistency, and outbound model privacy:

```bash
make quality-gates
```

The target runs only tests marked `quality` under `tests/quality/`. It uses the keyless mock SAR
drafter, deterministic metrics and checks, an in-memory OpenAI-compatible HTTP transport, and a
socket-denial guard. It requires no Infisical secret, provider credential, network access, or spend.
`make ci` and the reusable GitHub Actions quality job both invoke the same target.

## Acceptance thresholds

[`config/quality.yaml`](../../config/quality.yaml) is the single source for the quality limits:

| Metric | Required value | Interpretation |
|---|---:|---|
| Citation precision | `1.0` minimum | Every produced citation id was offered to the drafter. |
| Citation recall | `0.9` minimum | Expected scenario citations appear in the grounded draft. |
| Unsupported-claim recall | `0.95` minimum | Planted unsupported claims are flagged deterministically. |
| Clean-draft false-positive rate | `0.05` maximum | Supported claims are not incorrectly rejected. |
| Required-fact coverage | `0.95` minimum | Required verified case facts appear in rendered output. |

Metric implementations live in `fraudlens_ml.evaluation` and return frozen Pydantic results. The
same functions are reusable by later benchmark/report code so CI and performance studies cannot
silently adopt incompatible definitions.

## Suites and evidence

| Suite | What it proves |
|---|---|
| `test_citation_quality.py` | All 32 frozen synthetic SAR-evaluation scenarios traverse `MockSarDrafter` and `parse_and_ground`; citations remain inside the offered set and required facts meet policy. A stricter temporary config rejects a deliberately partial fixture. |
| `test_hallucination_detection.py` | Altered amounts/dates, invented typologies/identities and fabricated citations are planted; deterministic checks meet recall and clean-draft false-positive limits. |
| `test_model_egress.py` | Raw serialized request bytes contain no forbidden sentinels across direct SDK retry and provider fallback; disallowed source and corpus-digest mismatch stop before transport; agent tool results are allowlisted/aliased/remasked. |
| `test_published_study_consistency.py` | The bound docs/frontend study pair validates, scenario rows recompute summary means, and three quote-level judge samples recompute each unsupported-claim median. |
| `test_sar_cascade_quality.py` | The shipped gate rejects a citation-fabricating draft, the cascade escalates it, and an exhausted cascade fails with reason codes and no narrative. The corpus's recall floor is asserted against values derived from the 1,000-case run rather than typed into the fixture. |

The transport suite is the last process-local inspection point before a provider adapter. Its fake
endpoint is not evidence about an external provider's retention or contractual posture; provider
governance remains separately enforced by the LLM catalog and provider policy.

## Where the gate runs

The same evaluator runs in three places and nowhere else:

| Caller | When | What it decides |
|---|---|---|
| `QualityGatedSarDrafter` | every live draft, before persistence or SSE | whether the draft is served, escalated, or the cascade fails |
| The multi-agent graph | before the Compliance Reviewer | whether a revision is requested |
| The benchmark report | over persisted output, at report time | the published quality figures |

The third is checked against the first. A published cascade report must show that the verdict the
production drafter recorded at request time equals the verdict re-derived from the persisted output
on **every** attempt (`gate_verdict_parity`), or publication is refused. That parity is why the
benchmark's quality numbers are evidence about the shipped path rather than a second opinion about
it, and it is what allowed the v1 model-only measurement path to be retired in release 0.5.0.

A gate that repaired a draft would be worse than no gate: before 0.5.0 the single-writer path
silently deleted fabricated citation ids and returned something that looked clean. Nothing is
repaired now — a rejected draft escalates or fails, and its narrative is never emitted.

## Outbound provider posture

Hosted SAR routes go through OpenRouter only, over a named connection whose policy requires
zero-data-retention routing and denies provider data collection. Two rules make that durable:

- **Eligibility is revalidated at readiness, not trusted from static config.** Route eligibility on
  an aggregator is dynamic; a connection that asserted ZDR when it was written can stop qualifying.
  A run that exercises a hosted tier records the eligibility snapshot it observed into its published
  report provenance (`externalRoutes`).
- **An unexercised tier claims nothing.** No release 0.5.0 run exercised the hosted tier, so the
  published cascade report carries an empty `externalRoutes` and states so in its disclosures
  rather than restating the configured intent as a measurement.

Egress remains synthetic-only regardless: the hosted stage declares
`requires_egress_class: synthetic`, so a case whose persisted provenance is not synthetic cannot
reach it, and the cascade fails explicitly instead ([ADR-026](../architecture/adr/ADR-026-synthetic-only-model-egress.md),
[ADR-030](../architecture/adr/ADR-030-quality-gated-sar-model-cascade.md)).

## Failure handling

1. Run the failing test module directly with `uv run pytest <path> -q -o addopts='' -m quality`.
2. Identify whether the failure is a behavior regression, a deliberate contract change, or stale
   fixture/config evidence. Do not lower a threshold merely to make the suite green.
3. For egress failures, inspect the typed `SarModelInput` projection and persisted transaction
   source. Do not add a raw field or authorize `unknown`/`api-upload` as a shortcut.
4. For corpus failures, rebuild citations from the committed corpus using the configured chunk
   geometry. Never accept a label-only or mutable external snippet.
5. Re-run `make quality-gates`, then `make pre-pr` before handoff.

See [ADR-023](../architecture/adr/ADR-023-sar-quality-and-privacy-gates.md),
[ADR-026](../architecture/adr/ADR-026-synthetic-only-model-egress.md), and the
[PHI guardrails runbook](../runbooks/phi-guardrails.md) for decision and threat-model context.
