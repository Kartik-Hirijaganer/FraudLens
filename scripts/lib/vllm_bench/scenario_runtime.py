"""Summary: Production SAR scenario wiring for the versioned cascade live benchmark.

Key classes:
- (none)

Key functions:
- build_scenario_drafter: bind production app settings, the real client, and the daily budget guard.

Notes:
- The benchmark reaches vLLM through an SSH-terminated loopback URL. The client therefore uses its
  development transport setting solely to permit loopback HTTP; strict guardrails, PHI masking,
  raw-output denial, policy-downgrade denial, the production app overlay, and BudgetGuard remain
  enabled.
"""

from __future__ import annotations

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.factory import build_sar_drafter
from fraudlens_backend.settings import AppSettings
from fraudlens_llm import LlmClient, LlmSettings
from fraudlens_ml.sar import SarDrafter
from lib.vllm_bench.config import VllmBenchConfig


def _scenario_settings(config: VllmBenchConfig, profile: str) -> AppSettings:
    """Bind the production settings overlay and selected production SAR profile."""
    settings = AppSettings(
        llm_mode="live",
        sar_config_file=config.cascade.sar_config_file,
        sar_profile=profile,
    )
    if settings.environment != "prod":
        raise ValueError("scenario benchmark requires FRAUDLENS_ENVIRONMENT=prod")
    return settings


def _scenario_client() -> LlmClient:
    """Build the real client with only its SSH-terminated loopback transport exception enabled."""
    return LlmClient.from_settings(LlmSettings(environment="dev"))


def build_scenario_drafter(config: VllmBenchConfig, profile: str) -> SarDrafter:
    """Build the shipped scenario drafter with the production daily budget ceiling enforced."""
    settings = _scenario_settings(config, profile)
    return build_sar_drafter(
        settings,
        client=_scenario_client(),
        budget=BudgetGuard(daily_limit_usd=settings.llm_daily_budget_usd),
    )
