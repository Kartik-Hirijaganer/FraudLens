"""Summary: The deterministic AML rules engine (plan §16 Phase 4). `RuleRegistry` evaluates
a set of `RuleDefinition`s against one `RuleContext` and returns a `RuleEvaluation` — the
fired hits, a weighted subscore in [0, 1], a version fingerprint, and the codes of any rules
that faulted. Three properties matter and are tested (§17): it is **deterministic** (same
definitions + context ⇒ identical result), it has **per-rule fault isolation** (a rule whose
evaluator raises is recorded in `errored_rules` and skipped, never aborting the run), and its
**aggregation is weighted** (subscore = summed weight of fired rules ÷ summed weight of the
rules that evaluated cleanly, so a faulted rule neither inflates nor dilutes the score).
`merge_definitions` layers DB overrides onto the code defaults by `code` (later layer wins),
and `compute_rules_version` fingerprints a rule set so a version bump or enable/disable
changes the run's recorded `rules_version`.

Key classes:
- RuleRegistry: dispatches rule definitions to built-in evaluators and aggregates the result.

Key functions:
- merge_definitions: layer rule definitions by `code` (later layers override earlier ones).
- compute_rules_version: deterministic fingerprint of a rule set (code + version + enabled).

Notes:
- Evaluation catches `Exception` per rule on purpose (fault isolation): a buggy rule must not
  break an investigation — its code lands in `errored_rules` for the caller to log/audit.
- The subscore denominator excludes both disabled and faulted rules, so it depends only on
  the rules that actually ran — keeping the score deterministic and meaningful.
- A definition whose `rule_type` has no registered evaluator is treated as a fault (skipped),
  so an unknown/forward-declared type degrades gracefully rather than raising.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal

from fraudlens_core.rules.base import (
    AmlRuleType,
    RuleContext,
    RuleDefinition,
    RuleEvaluation,
    RuleEvaluator,
    RuleHit,
)
from fraudlens_core.rules.builtins import BUILTIN_RULES

_SUBSCORE_QUANTUM = Decimal("0.0001")
_VERSION_FINGERPRINT_LEN = 16


def merge_definitions(*layers: Sequence[RuleDefinition]) -> tuple[RuleDefinition, ...]:
    """Merge rule definitions by `code` across layers (later layers override earlier ones).

    The first layer is the code defaults; later layers are DB rows (global, then per-agency),
    so a DB row replaces a default with the same code, and an agency row replaces a global
    one. The result is sorted by `code` for a stable, deterministic ordering.
    """
    merged: dict[str, RuleDefinition] = {}
    for layer in layers:
        for definition in layer:
            merged[definition.code] = definition
    return tuple(sorted(merged.values(), key=lambda definition: definition.code))


def compute_rules_version(definitions: Sequence[RuleDefinition]) -> str:
    """Return a deterministic fingerprint of a rule set (changes on version/enable edits)."""
    payload = sorted(
        [definition.code, definition.version, definition.enabled] for definition in definitions
    )
    canonical = json.dumps(payload, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_VERSION_FINGERPRINT_LEN]


class RuleRegistry:
    """Dispatches rule definitions to built-in evaluators and aggregates a RuleEvaluation."""

    def __init__(self, evaluators: Mapping[AmlRuleType, RuleEvaluator] | None = None) -> None:
        """Bind the dispatch table (defaults to the built-in rules; overridable in tests)."""
        self._evaluators: dict[AmlRuleType, RuleEvaluator] = dict(
            evaluators if evaluators is not None else BUILTIN_RULES
        )

    def evaluate(
        self, definitions: Sequence[RuleDefinition], context: RuleContext
    ) -> RuleEvaluation:
        """Evaluate enabled rules against the context (fault-isolated, weighted aggregation)."""
        hits: list[RuleHit] = []
        errored: list[str] = []
        evaluated_weight = Decimal("0")
        fired_weight = Decimal("0")
        for definition in definitions:
            if not definition.enabled:
                continue
            evaluator = self._evaluators.get(definition.rule_type)
            if evaluator is None:
                errored.append(definition.code)
                continue
            try:
                hit = evaluator(definition, context)
            except Exception:
                errored.append(definition.code)
                continue
            evaluated_weight += definition.weight
            if hit is not None:
                hits.append(hit)
                fired_weight += definition.weight
        return RuleEvaluation(
            hits=tuple(hits),
            subscore=self._aggregate(fired_weight, evaluated_weight),
            rules_version=compute_rules_version(definitions),
            errored_rules=tuple(errored),
        )

    @staticmethod
    def _aggregate(fired_weight: Decimal, evaluated_weight: Decimal) -> Decimal:
        """Return the weighted subscore in [0, 1] (0 when no rule evaluated cleanly)."""
        if evaluated_weight <= 0:
            return Decimal("0")
        return (fired_weight / evaluated_weight).quantize(_SUBSCORE_QUANTUM)
