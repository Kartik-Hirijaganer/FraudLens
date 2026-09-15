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

The transport suite is the last process-local inspection point before a provider adapter. Its fake
endpoint is not evidence about an external provider's retention or contractual posture; provider
governance remains separately enforced by the LLM catalog and provider policy.

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
