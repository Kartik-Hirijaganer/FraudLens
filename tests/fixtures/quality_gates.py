"""Shared `SarQualityGate` builders for tests that inject a drafter directly.

`production_gate()` binds the committed `config/quality.yaml` policy — use it whenever a test's
subject IS the gate or the end-to-end acceptance of a draft. `grounding_gate()` binds a narrower
policy that keeps only the citation and claim-evidence rules: the bounded multi-agent graph tests
script writer payloads to exercise routing and grounding, not SAR narrative shape, so requiring
FinCEN sections and asserted facts there would assert something those fixtures never claimed.
`replay_gate()` binds the committed `sar_quality.replay_gate` policy — the citation-membership
subset the REAL persisted run can be judged under:
those outputs were produced by prompt v1, which never asked for `claims` or asserted facts, so the
claim-evidence, fact-matching and FinCEN rules would reject all 1,000 of them and measure the old
prompt rather than the gate. It is the policy the committed replay corpus's verdicts were recorded
under, and it is what reproduces the published 73.9% / 94.1% arm pass rates.
"""

from __future__ import annotations

from fraudlens_backend.sar.quality_gate import (
    REPLAY_GATE_POLICY,
    SarQualityGate,
    SarRuntimeGatePolicy,
    load_sar_gate_policy,
)

_MAX_TIERS = 3


def production_gate() -> SarQualityGate:
    """Return a gate bound to the committed runtime policy."""
    return SarQualityGate(load_sar_gate_policy())


def grounding_gate() -> SarQualityGate:
    """Return a gate that judges citation membership and claim evidence only."""
    return SarQualityGate(
        SarRuntimeGatePolicy(
            policy_version="sar-gate-grounding-test",
            require_citation=True,
            allow_duplicate_citations=False,
            require_claim_evidence=True,
            require_asserted_fact_match=False,
            require_fincen_elements=False,
            fail_on_truncation=False,
            max_tiers=_MAX_TIERS,
        )
    )


def replay_gate() -> SarQualityGate:
    """Judge real prompt-v1 output by citation membership, duplication, and truncation only."""
    return SarQualityGate(load_sar_gate_policy(policy=REPLAY_GATE_POLICY))
