"""Summary: Deterministic prompt-risk scanning for injection, system prompt
extraction, secret exfiltration, tool misuse, data exfiltration, and encoded payload
patterns. The scanner returns metadata only and never logs prompt text.

Key classes:
- (none)

Key functions:
- scan_prompt_risk: Scan masked prompt text and return a guardrail outcome.

Notes:
- analysis/extraction task types downgrade artifact-internal injection hits to flag.
"""

from __future__ import annotations

import re
from typing import Literal

from fraudlens_llm.models import Finding, GuardrailDecision, ScanOutcome, Strictness, TaskType

_Severity = Literal["low", "medium", "high", "critical"]
_FINDING_PATTERNS: tuple[tuple[str, _Severity, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        "high",
        re.compile(
            r"\b(ignore|override|bypass)\b.{0,40}\b(instruction|policy|guardrail)s?\b",
            re.I,
        ),
    ),
    (
        "system_prompt_extraction",
        "high",
        re.compile(
            r"\b(reveal|print|show|dump)\b.{0,40}\b(system prompt|hidden instruction)s?\b",
            re.I,
        ),
    ),
    (
        "secret_exfiltration",
        "critical",
        re.compile(r"\b(api key|password|secret|token|private key|env var|credential)s?\b", re.I),
    ),
    (
        "tool_misuse",
        "high",
        re.compile(r"\b(run|execute|shell|terminal|delete files?|curl|wget)\b", re.I),
    ),
    (
        "data_exfiltration",
        "critical",
        re.compile(
            r"\b(exfiltrate|send|upload|leak)\b.{0,40}\b(data|records?|tenant|database)\b",
            re.I,
        ),
    ),
    (
        "encoded_payload",
        "medium",
        re.compile(r"\b(base64|rot13|hex encoded|decode this|[A-Za-z0-9+/]{80,}={0,2})\b", re.I),
    ),
)


def scan_prompt_risk(
    text: str,
    *,
    strictness: Strictness,
    task_type: TaskType,
) -> ScanOutcome:
    """Scan masked prompt text and return a deterministic outcome."""
    if strictness == Strictness.DISABLED:
        return ScanOutcome(decision=GuardrailDecision.ALLOW, findings=[])

    findings = [
        Finding(category=category, severity=severity, location="input")
        for category, severity, pattern in _FINDING_PATTERNS
        if pattern.search(text)
    ]
    if not findings:
        return ScanOutcome(decision=GuardrailDecision.ALLOW, findings=[])
    if strictness == Strictness.FLAG or task_type in {TaskType.ANALYSIS, TaskType.EXTRACTION}:
        return ScanOutcome(decision=GuardrailDecision.FLAG, findings=findings)
    return ScanOutcome(decision=GuardrailDecision.BLOCK, findings=findings)
