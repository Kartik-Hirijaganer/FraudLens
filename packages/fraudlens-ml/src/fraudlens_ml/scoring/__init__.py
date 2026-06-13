"""fraudlens-ml scoring: deterministic features, XGBoost scoring, SHAP explainability,
artifact persistence + pointer-keyed loading (last-known-good), canary routing, and the
§10.5.1 quantitative promotion gates (plan §16 Phase 5). Re-exports are intentional."""

from __future__ import annotations

from fraudlens_ml.scoring.artifacts import (
    ArtifactError,
    Calibration,
    DeploymentPointer,
    LoadedArtifact,
    ModelArtifactMetadata,
    ModelCache,
    load_artifact,
    save_artifact,
)
from fraudlens_ml.scoring.explainer import Explainer, Explanation, FeatureContribution
from fraudlens_ml.scoring.features import (
    FEATURE_NAMES,
    FEATURE_SPEC_VERSION,
    FeatureSpec,
    current_feature_spec,
    extract_feature_vector,
    extract_features,
    feature_vector,
)
from fraudlens_ml.scoring.gates import (
    CandidateMetrics,
    GateCheck,
    GateReport,
    ModelGates,
    compute_metrics,
    evaluate_gates,
)
from fraudlens_ml.scoring.router import CanaryDeployment, CanaryRouter, RoutingDecision
from fraudlens_ml.scoring.scorer import ScoreOutput, Scorer

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SPEC_VERSION",
    "ArtifactError",
    "Calibration",
    "CanaryDeployment",
    "CanaryRouter",
    "CandidateMetrics",
    "DeploymentPointer",
    "Explainer",
    "Explanation",
    "FeatureContribution",
    "FeatureSpec",
    "GateCheck",
    "GateReport",
    "LoadedArtifact",
    "ModelArtifactMetadata",
    "ModelCache",
    "ModelGates",
    "RoutingDecision",
    "ScoreOutput",
    "Scorer",
    "compute_metrics",
    "current_feature_spec",
    "evaluate_gates",
    "extract_feature_vector",
    "extract_features",
    "feature_vector",
    "load_artifact",
    "save_artifact",
]
