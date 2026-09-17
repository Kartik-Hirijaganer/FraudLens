"""Unit tests for SAR cascade routing: profile selection and named provider connections.

Release 0.5.0 AD-2.5: one `vllm` governance entry with one base URL resolved both self-hosted
tiers to the same endpoint, so the cascade could not exist. A stage now names a CONNECTION, which
inherits governance posture unchanged and overrides only the env references that carry its URL
and key.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from fraudlens_backend.sar.factory import (
    SarLlmConfig,
    SarProfileNotSelectedError,
    load_sar_llm_config,
)
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import load_providers
from fraudlens_llm.exceptions import CatalogError, ProviderNotConfiguredError
from fraudlens_llm.providers import Providers

_PROVIDERS = find_config_dir() / "llm" / "providers.yml"


def test_the_single_model_config_still_builds_one_stage() -> None:
    """A config with `model` behaves exactly as before: one stage, no cascade, no flag."""
    config = load_sar_llm_config(find_config_dir() / "llm" / "sar.yml")
    stages = config.stages(None)

    assert len(stages) == 1
    assert stages[0].model == config.model
    assert stages[0].connection is None
    assert stages[0].constrained_decoding is False


def test_the_committed_cascade_profiles_are_ordered_and_routed() -> None:
    config = load_sar_llm_config(find_config_dir() / "llm" / "sar-vllm.yml")

    assert set(config.profiles) == {
        "deployed-openrouter",
        "bf16-baseline",
        "awq-raw",
        "awq-constrained",
        "bf16-constrained",
        "awq-bf16-unconstrained",
        "awq-bf16",
        "awq-bf16-external",
    }
    cascade = config.stages("awq-bf16-external")
    assert [stage.name for stage in cascade] == ["awq", "bf16", "external"]
    assert [stage.connection for stage in cascade] == [
        "runpod-awq",
        "runpod-bf16",
        "openrouter-zdr",
    ]
    assert all(stage.constrained_decoding for stage in cascade)
    assert cascade[-1].requires_egress_class == "synthetic"

    for profile, connection in (
        ("awq-constrained", "runpod-awq"),
        ("bf16-constrained", "runpod-bf16"),
    ):
        control = config.stages(profile)
        assert len(control) == 1
        assert control[0].connection == connection
        assert control[0].constrained_decoding is True


def test_the_two_cascade_shapes_differ_only_in_constrained_decoding() -> None:
    """Scenario 3 versus scenario 4 isolates the schema, so both must be real routes."""
    config = load_sar_llm_config(find_config_dir() / "llm" / "sar-vllm.yml")
    constrained = config.stages("awq-bf16")
    unconstrained = config.stages("awq-bf16-unconstrained")

    assert [stage.name for stage in unconstrained] == [stage.name for stage in constrained]
    assert [stage.model for stage in unconstrained] == [stage.model for stage in constrained]
    assert [stage.connection for stage in unconstrained] == [
        stage.connection for stage in constrained
    ]
    assert all(stage.constrained_decoding for stage in constrained)
    assert not any(stage.constrained_decoding for stage in unconstrained)


def test_the_deployed_profile_is_a_single_hosted_stage() -> None:
    """The always-on shape needs no RunPod endpoint and behaves as one model."""
    stages = load_sar_llm_config(find_config_dir() / "llm" / "sar-vllm.yml").stages(
        "deployed-openrouter"
    )

    assert len(stages) == 1
    assert stages[0].connection == "openrouter-zdr"


@pytest.mark.parametrize("profile", [None, "", "not-a-profile"])
def test_a_missing_or_unknown_profile_fails_closed(profile) -> None:
    config = load_sar_llm_config(find_config_dir() / "llm" / "sar-vllm.yml")

    with pytest.raises(SarProfileNotSelectedError):
        config.stages(profile)


def test_a_config_declaring_both_shapes_is_refused() -> None:
    """A stale `model` must never silently shadow the profile a deploy selected."""
    with pytest.raises(ValidationError):
        SarLlmConfig.model_validate(
            {
                "model": "openrouter/openai/gpt-5-mini",
                "max_output_tokens": 100,
                "profiles": {"x": [{"name": "a", "model": "openrouter/openai/gpt-5-mini"}]},
            }
        )
    with pytest.raises(ValidationError):
        SarLlmConfig.model_validate({"max_output_tokens": 100})
    with pytest.raises(ValidationError):
        SarLlmConfig.model_validate({"max_output_tokens": 100, "profiles": {"empty": []}})


def test_named_connections_inherit_governance_and_override_only_transport() -> None:
    providers = load_providers(_PROVIDERS)
    base = providers.get("vllm")
    awq = providers.route("vllm", "runpod-awq")
    bf16 = providers.route("vllm", "runpod-bf16")

    assert (awq.base_url_env, awq.api_key_env) == ("VLLM_AWQ_BASE_URL", "VLLM_AWQ_API_KEY")
    assert (bf16.base_url_env, bf16.api_key_env) == ("VLLM_BF16_BASE_URL", "VLLM_BF16_API_KEY")
    assert awq.base_url_env != bf16.base_url_env  # the whole point of AD-2.5
    for route in (awq, bf16):
        assert route.allowed_data_classes == base.allowed_data_classes
        assert (route.region, route.data_retention) == (base.region, base.data_retention)
        assert route.zdr_supported == base.zdr_supported


def test_the_hosted_connection_carries_mandatory_zero_data_retention() -> None:
    providers = load_providers(_PROVIDERS)
    route = providers.route("openrouter", "openrouter-zdr")
    connection = providers.connection("openrouter-zdr")

    assert route.request_options == {"provider": {"zdr": True, "data_collection": "deny"}}
    assert providers.get("openrouter").request_options == {}  # only the named route enforces it
    assert set(connection.allowed_upstreams) == {"openai", "anthropic", "azure"}


def test_an_unknown_or_mismatched_connection_fails_closed() -> None:
    providers = load_providers(_PROVIDERS)

    with pytest.raises(ProviderNotConfiguredError):
        providers.connection("runpod-nonexistent")
    with pytest.raises(ProviderNotConfiguredError):
        providers.route("openrouter", "runpod-awq")  # the route does not serve that provider


def test_a_connection_naming_an_unconfigured_provider_is_rejected(tmp_path) -> None:
    path = tmp_path / "providers.yml"
    payload = {
        "providers": yaml.safe_load(_PROVIDERS.read_text(encoding="utf-8"))["providers"],
        "connections": {"orphan": {"provider": "not-configured"}},
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(CatalogError):
        load_providers(path)


def test_a_flat_provider_file_without_connections_still_loads(tmp_path) -> None:
    """Provider files that declare no routes keep working; connections are additive."""
    flat = yaml.safe_load(_PROVIDERS.read_text(encoding="utf-8"))["providers"]
    path = tmp_path / "providers.yml"
    path.write_text(yaml.safe_dump(flat), encoding="utf-8")

    providers = load_providers(path)

    assert isinstance(providers, Providers)
    assert providers.connections == {}
    assert providers.route("vllm", None).base_url_env == "VLLM_BASE_URL"
