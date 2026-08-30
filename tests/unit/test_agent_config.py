"""Unit tests for strict multi-agent YAML, catalog, and tool-registry validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fraudlens_backend.agents.config import (
    AgentConfig,
    AgentRole,
    AgentsConfigError,
    load_agents_config,
)
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import Catalog, load_catalog

_TOOLS = {
    "transaction_history",
    "rule_hits",
    "shap_drivers",
    "alert_history",
    "regulation_search",
}


def _catalog() -> Catalog:
    return load_catalog(find_config_dir() / "llm" / "catalog.yml")


def _write_config(tmp_path: Path, text: str) -> Path:
    target = tmp_path / "agents.yml"
    target.write_text(text, encoding="utf-8")
    return target


def test_committed_agent_config_is_complete_frozen_and_catalog_validated() -> None:
    config = load_agents_config(catalog=_catalog(), available_tools=_TOOLS)

    assert config.graph_version == "agents-v1"
    assert [role for role, _agent in config.agents.items()] == list(AgentRole)
    assert config.agents.for_role(AgentRole.EVIDENCE_INVESTIGATOR).max_tool_calls == 6
    assert config.agents.for_role(AgentRole.SAR_WRITER).tools == ()
    assert config.workflow.max_revisions == 1
    assert config.workflow.fallback_to_single_writer is True
    assert config.quotas.live_runs_total_per_day == 10
    with pytest.raises(ValidationError):
        config.graph_version = "changed"  # type: ignore[misc]


def test_agent_config_rejects_unbounded_or_duplicate_tool_allowlists() -> None:
    common = {
        "model": "openrouter/example/model",
        "prompt_id": "v1",
        "max_output_tokens": 10,
    }
    with pytest.raises(ValidationError, match="max_tool_calls"):
        AgentConfig(**common, tools=["lookup"])
    with pytest.raises(ValidationError, match="duplicate"):
        AgentConfig(**common, max_tool_calls=2, tools=["lookup", "lookup"])


def test_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    committed = (find_config_dir() / "llm" / "agents.yml").read_text(encoding="utf-8")
    duplicate = committed.replace(
        "graph_version: agents-v1",
        "graph_version: agents-v1\ngraph_version: agents-other",
        1,
    )

    with pytest.raises(AgentsConfigError) as raised:
        load_agents_config(
            catalog=_catalog(),
            available_tools=_TOOLS,
            path=_write_config(tmp_path, duplicate),
        )
    assert "duplicate key" in str(raised.value.__cause__)


def test_loader_fails_closed_for_missing_tool_and_file(tmp_path: Path) -> None:
    with pytest.raises(AgentsConfigError, match="invalid"):
        load_agents_config(catalog=_catalog(), available_tools=set())
    with pytest.raises(AgentsConfigError, match="invalid"):
        load_agents_config(
            catalog=_catalog(),
            available_tools=_TOOLS,
            path=tmp_path / "absent.yml",
        )


@pytest.mark.parametrize(
    ("card_update", "expected"),
    [
        ({"callable": False}, "non-callable"),
        ({"structured_output": False}, "structured output"),
        ({"tool_calling": False}, "tool calling"),
        ({"output_price_per_million": None}, "pricing"),
    ],
)
def test_loader_fails_closed_for_catalog_capability_drift(
    card_update: dict[str, object],
    expected: str,
) -> None:
    catalog = _catalog()
    providers = {provider: dict(cards) for provider, cards in catalog.providers.items()}
    original = providers["openrouter"]["x-ai/grok-4.3"]
    providers["openrouter"]["x-ai/grok-4.3"] = original.model_copy(update=card_update)

    with pytest.raises(AgentsConfigError) as raised:
        load_agents_config(
            catalog=Catalog(providers=providers),
            available_tools=_TOOLS,
        )
    assert expected in str(raised.value.__cause__)


def test_loader_fails_closed_for_unknown_model(tmp_path: Path) -> None:
    committed = (find_config_dir() / "llm" / "agents.yml").read_text(encoding="utf-8")
    changed = committed.replace(
        "openrouter/x-ai/grok-4.3",
        "openrouter/missing/model",
        1,
    )

    with pytest.raises(AgentsConfigError) as raised:
        load_agents_config(
            catalog=_catalog(),
            available_tools=_TOOLS,
            path=_write_config(tmp_path, changed),
        )
    assert "unknown model" in str(raised.value.__cause__)
