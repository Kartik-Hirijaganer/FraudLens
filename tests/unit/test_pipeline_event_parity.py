"""Guard value parity between the pipeline and persisted analysis-run event enums."""

from __future__ import annotations

from fraudlens_backend.db.models.enums import AnalysisRunEventType
from fraudlens_ml.pipeline.events import PipelineEventType
from fraudlens_ml.sar import SarEventType


def test_pipeline_and_persisted_event_values_are_identical() -> None:
    pipeline_values = {event.value for event in PipelineEventType}
    persisted_values = {event.value for event in AnalysisRunEventType}

    assert pipeline_values == persisted_values
    assert SarEventType.AGENT_TOOL_COMPLETED.value not in pipeline_values
