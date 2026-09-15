"""Summary: Verify strict loading of the shared SAR quality and model-egress policy reference.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Tests use temporary, non-secret YAML policies and never contact a provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.quality.config import QualityConfigError, load_quality_config


def test_committed_quality_thresholds_are_exact() -> None:
    config = load_quality_config()

    assert config.sar_quality.citation_precision_min == 1.0
    assert config.sar_quality.citation_recall_min == 0.9
    assert config.sar_quality.unsupported_claim_recall_min == 0.95
    assert config.sar_quality.clean_draft_false_positive_max == 0.05
    assert config.sar_quality.required_fact_coverage_min == 0.95
    assert config.egress_policy_file == "config/llm/egress.yml"


@pytest.mark.parametrize(
    "contents",
    (
        "not: [valid",
        "file_length: {}",
        "file_length: {}\nsar_quality: {}\negress_policy_file: x\nunknown: true\n",
    ),
)
def test_invalid_quality_policy_fails_closed(tmp_path: Path, contents: str) -> None:
    policy = tmp_path / "quality.yaml"
    policy.write_text(contents, encoding="utf-8")

    with pytest.raises(QualityConfigError, match="Quality configuration is invalid"):
        load_quality_config(policy)


def test_missing_quality_policy_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(QualityConfigError, match="Quality configuration is invalid"):
        load_quality_config(tmp_path / "missing.yaml")
