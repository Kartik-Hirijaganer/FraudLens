"""fraudlens-ml: ML scoring + RAG (heavy ML/RAG deps isolated here, plan §16). Phase 5 ships
the `scoring` package (XGBoost scoring, SHAP, artifact loading + pointer resolution, canary
routing, promotion gates); Phase 6 ships the `rag` package (FinCEN/BSA corpus ingest, ChromaDB
index, retriever with lexical fallback, citation + injection-as-data defense). Import each
subpackage explicitly (`fraudlens_ml.scoring`, `fraudlens_ml.rag`) so importing this top package
stays cheap (xgboost/shap/sklearn and chromadb load only when their subpackage is used).
Layering: imports fraudlens-core only, never fraudlens-backend or fraudlens-llm."""

from __future__ import annotations

__all__: list[str] = []
