"""Background-job runners executed off the request path (plan §16 Phase 8+).

Phase 8 ships the batch investigation runner (`runner.py`): it drives the LangGraph pipeline
over a set of transactions headlessly (no SSE, no RunManager) and records a `job_executions`
row. Training/retrain/drift Jobs land in later phases; the local-vs-Container-Apps dispatch
seam is `fraudlens_backend.backends.jobs`.
"""
