"""Summary: Heavy implementation adapters for the investigation pipeline's light ports.

Key classes:
- RulesAdapter: adapt rule evaluation onto RulesPort.
- ScorerAdapter: adapt deployed model scoring onto ScorerPort.
- ExplainerAdapter: adapt SHAP explanations onto ExplainerPort.
- RetrieverAdapter: adapt grounded retrieval onto RetrieverPort.

Key functions:
- (none)

Notes:
- The underscored path helpers remain re-exported by pipeline_wiring for compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fraudlens_backend.settings import find_config_dir
from fraudlens_core import RuleContext, RuleEvaluation, RuleRegistry
from fraudlens_ml.pipeline import RagResult, ScoreResult, ShapResult
from fraudlens_ml.rag import Retriever, build_rag_context, extract_citations
from fraudlens_ml.sar import SarCitation, SarFeature
from fraudlens_ml.scoring import DeploymentPointer, Explainer, ModelCache, Scorer


def _anchored(path_value: str) -> Path:
    """Resolve a config path; a relative value anchors at the process CWD (repo root / /app)."""
    path = Path(path_value)
    return path if path.is_absolute() else Path.cwd() / path


def _config_anchored(path_value: str) -> Path:
    """Resolve a relative path below config/, rejecting absolute paths and traversal."""
    path = Path(path_value)
    if path.is_absolute():
        raise ValueError("Multi-agent configuration must be relative to the config directory")
    base = find_config_dir().resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("Multi-agent configuration must remain below the config directory")
    return resolved


# --------------------------------------------------------------------------------------------------
# Port adapters: map the real heavy implementations onto the pipeline's light protocols.
# --------------------------------------------------------------------------------------------------


class RulesAdapter:
    """Adapts the pure `RuleRegistry` + the agency's merged rule set onto `RulesPort`."""

    def __init__(self, registry: RuleRegistry, definitions: tuple[Any, ...]) -> None:
        """Bind the rules engine and the resolved (defaults < global < agency) definitions."""
        self._registry = registry
        self._definitions = definitions

    def evaluate(self, context: RuleContext) -> RuleEvaluation:
        """Evaluate the deterministic rules engine for the context (fault-isolated)."""
        return self._registry.evaluate(self._definitions, context)


class ScorerAdapter:
    """Adapts the heavy `Scorer` (+ the routed deployment pointer) onto `ScorerPort`."""

    def __init__(
        self, scorer: Scorer, pointer: DeploymentPointer | None, *, was_canary: bool = False
    ) -> None:
        """Bind the scorer, the resolved (active or canary-routed) pointer, and the canary flag."""
        self._scorer = scorer
        self._pointer = pointer
        self._was_canary = was_canary

    def score(self, context: RuleContext) -> ScoreResult:
        """Score via the routed model; raise when no deployment exists (→ run.failed).

        `was_canary` is the per-run routing decision (plan §10.5); it flows onto the `ScoreResult`
        so the scoring step's hash-only inference log records which arm scored ("logs both").
        """
        if self._pointer is None:
            raise RuntimeError("no active model deployment")
        output = self._scorer.score(self._pointer, context)
        return ScoreResult(
            fraud_probability=output.fraud_probability,
            model_version_label=output.model_version_label,
            was_canary=self._was_canary,
            risk_thresholds=output.risk_thresholds,
        )


class ExplainerAdapter:
    """Adapts the heavy SHAP `Explainer` (+ the model cache) onto `ExplainerPort`."""

    def __init__(
        self, explainer: Explainer, cache: ModelCache, pointer: DeploymentPointer | None
    ) -> None:
        """Bind the explainer, the artifact cache, and the active deployment pointer."""
        self._explainer = explainer
        self._cache = cache
        self._pointer = pointer

    def explain(self, context: RuleContext) -> ShapResult:
        """Explain the same model that scored; raise when no deployment exists (→ run.failed)."""
        if self._pointer is None:
            raise RuntimeError("no active model deployment")
        loaded = self._cache.get(self._pointer)
        explanation = self._explainer.explain(loaded, context)
        return ShapResult(
            base_value=explanation.base_value,
            shap_values=dict(explanation.shap_values),
            top_features=tuple(
                SarFeature(feature=item.feature, value=item.value, shap_value=item.shap_value)
                for item in explanation.top_features
            ),
        )


class RetrieverAdapter:
    """Adapts the heavy `Retriever` (+ citation extraction + fencing) onto `RetrieverPort`."""

    def __init__(self, retriever: Retriever) -> None:
        """Bind the FinCEN/BSA retriever the investigation cites from."""
        self._retriever = retriever

    def retrieve(self, query: str, *, top_k: int) -> RagResult:
        """Retrieve grounded citations + the escaped fenced context for the SAR prompt."""
        result = self._retriever.retrieve(query, top_k=top_k)
        citations = tuple(
            SarCitation(
                citation=item.citation, title=item.title, source=item.source, snippet=item.snippet
            )
            for item in extract_citations(result.chunks)
        )
        return RagResult(
            citations=citations,
            rag_context=build_rag_context(result.chunks),
            mode=result.mode,
            rag_version=result.rag_version,
            chunks=tuple(chunk.model_dump(mode="json") for chunk in result.chunks),
        )
