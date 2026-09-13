"""FraudLens memory-bounded IBM full-data training pipeline (ADR-025)."""

from lib.fulldata.config import (
    DEFAULT_FULLDATA_CONFIG,
    FullDataConfig,
    load_fulldata_config,
)

__all__ = ["DEFAULT_FULLDATA_CONFIG", "FullDataConfig", "load_fulldata_config"]
