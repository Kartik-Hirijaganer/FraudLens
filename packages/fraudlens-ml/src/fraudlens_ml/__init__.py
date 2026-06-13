"""fraudlens-ml: ML scoring + RAG (heavy ML/RAG deps isolated here, plan §16). Phase 5 ships
the `scoring` package (XGBoost scoring, SHAP, artifact loading + pointer resolution, canary
routing, promotion gates); RAG lands in Phase 6. Import it explicitly as `fraudlens_ml.scoring`
so importing this top package stays cheap (xgboost/shap/sklearn load only when scoring is used).
Layering: imports fraudlens-core only, never fraudlens-backend or fraudlens-llm."""

from __future__ import annotations

__all__: list[str] = []
