"""Async data-access repositories. `TenantScopedRepository` enforces `agency_id` isolation
for tenant tables; `AgencyRepository` resolves the platform `agencies` table;
`RuleRepository` does agency-scoped CRUD over `aml_rules` (nullable agency_id) + loads the
merged engine rule set; `ModelRegistryRepository` reads the platform model registry + resolves
the active deployment pointer; `ModelLifecycleRepository` owns the platform lifecycle WRITES
(shadow/approve/canary/activate/rollback + canary inference stats); `SarDraftRepository`
persists/looks up agency-scoped SAR drafts;
`AnalysisRunRepository` persists an investigation run + its events/results/retrievals/inference/
alerts (the pipeline `RunStore` seam); `AlertRepository` drives the alert/review workflow
(actions, transitions, resolution labels); `DashboardRepository` computes the tenant-scoped
dashboard aggregate (counts + cost + model health); `UserRepository` resolves/provisions
tenant-scoped auth users; `AuditLogRepository` writes the PHI-free audit trail. Re-exports are
intentional (see members)."""

from __future__ import annotations

from fraudlens_backend.db.repositories.agencies import AgencyRepository
from fraudlens_backend.db.repositories.agents import AgentExecutionRepository
from fraudlens_backend.db.repositories.alerts import AlertRepository
from fraudlens_backend.db.repositories.analysis import AnalysisRunRepository
from fraudlens_backend.db.repositories.audit import AuditLogRepository
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_backend.db.repositories.dashboard import DashboardData, DashboardRepository
from fraudlens_backend.db.repositories.model_lifecycle import ModelLifecycleRepository
from fraudlens_backend.db.repositories.model_registry import ModelRegistryRepository
from fraudlens_backend.db.repositories.rules import RuleRepository
from fraudlens_backend.db.repositories.runtime_config import (
    RuntimeFeatureFlags,
    load_feature_flags,
    load_llm_daily_budget_usd,
)
from fraudlens_backend.db.repositories.sar import SarDraftRepository
from fraudlens_backend.db.repositories.transactions import (
    IngestOutcome,
    TransactionRepository,
)
from fraudlens_backend.db.repositories.users import UserRepository

__all__ = [
    "AgencyRepository",
    "AgentExecutionRepository",
    "AlertRepository",
    "AnalysisRunRepository",
    "AuditLogRepository",
    "DashboardData",
    "DashboardRepository",
    "IngestOutcome",
    "ModelLifecycleRepository",
    "ModelRegistryRepository",
    "RuleRepository",
    "RuntimeFeatureFlags",
    "SarDraftRepository",
    "TenantScopedRepository",
    "TransactionRepository",
    "UserRepository",
    "load_feature_flags",
    "load_llm_daily_budget_usd",
]
