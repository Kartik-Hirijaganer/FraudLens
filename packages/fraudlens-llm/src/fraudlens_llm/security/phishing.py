"""Summary: Deterministic raw-output scanner for phishing, secret solicitation,
payment solicitation, MFA solicitation, and script payloads. It runs before output
sanitization so decisions are based on the true provider text.

Key classes:
- (none)

Key functions:
- scan_output_risk: Scan raw provider output and return a guardrail outcome.

Notes:
- analysis/extraction receive descriptive leniency, but unsafe generation is blocked.
"""

from __future__ import annotations

import re
from typing import Literal

from fraudlens_llm.models import Finding, GuardrailDecision, ScanOutcome, Strictness, TaskType

_Severity = Literal["low", "medium", "high", "critical"]
_OUTPUT_PATTERNS: tuple[tuple[str, _Severity, re.Pattern[str]], ...] = (
    ("script_payload", "critical", re.compile(r"<\s*script\b|javascript\s*:|onerror\s*=", re.I)),
    (
        "secret_solicitation",
        "critical",
        re.compile(
            r"\b(send|enter|provide|share|confirm|asks? for)\b.{0,40}\b"
            r"(password|api key|token)\b",
            re.I,
        ),
    ),
    (
        "mfa_solicitation",
        "critical",
        re.compile(
            r"\b(send|enter|provide|share|confirm)\b.{0,40}\b(MFA|2FA|verification code)\b",
            re.I,
        ),
    ),
    (
        "payment_solicitation",
        "high",
        re.compile(
            r"\b(send|wire|pay|transfer)\b.{0,50}\b(card|payment|bitcoin|crypto|account)\b",
            re.I,
        ),
    ),
)
_DESCRIPTIVE_RE = re.compile(
    r"\b(analysis|describes?|example|warning|indicator|the message asks|do not)\b", re.I
)


def scan_output_risk(
    text: str,
    *,
    strictness: Strictness,
    task_type: TaskType,
) -> tuple[ScanOutcome, ScanOutcome]:
    """Return output-policy and phishing outcomes for raw provider output."""
    if strictness == Strictness.DISABLED:
        allow = ScanOutcome(decision=GuardrailDecision.ALLOW, findings=[])
        return allow, allow

    findings = [
        Finding(category=category, severity=severity, location="output")
        for category, severity, pattern in _OUTPUT_PATTERNS
        if pattern.search(text)
    ]
    if not findings:
        allow = ScanOutcome(decision=GuardrailDecision.ALLOW, findings=[])
        return allow, allow

    decision = _decision_for_findings(text, strictness=strictness, task_type=task_type)
    output_findings = [finding for finding in findings if finding.category == "script_payload"]
    phishing_findings = [finding for finding in findings if finding.category != "script_payload"]
    return (
        ScanOutcome(decision=decision, findings=output_findings),
        ScanOutcome(decision=decision, findings=phishing_findings),
    )


def _decision_for_findings(
    text: str,
    *,
    strictness: Strictness,
    task_type: TaskType,
) -> GuardrailDecision:
    """Return the output decision for matched findings."""
    if strictness == Strictness.FLAG:
        return GuardrailDecision.FLAG
    if task_type in {TaskType.ANALYSIS, TaskType.EXTRACTION} and _DESCRIPTIVE_RE.search(text):
        return GuardrailDecision.FLAG
    return GuardrailDecision.BLOCK
