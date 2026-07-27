"""Summary: Read-only verification of the portfolio demo story against `config/portfolio-demo.yaml`
(plan §16 Phase 6). It answers one question — does the database actually hold the story the config
declares? — by re-reading live state and comparing it with the pinned `expected` block: the
transaction count and scored/unscored split, per-band counts (`risk_band IS NULL` is `unscored`,
matching `dashboard.py`), alert- and SAR-state counts, each scenario's expected band AND expected
rule codes (re-read from the immutable `analysis_results` snapshot), the active model label, the
run/result/inference/event provenance every scored row must carry, and the action, audit, and
training-label history. Every expectation is READ from the config; none is written here, so a
mismatch is reported rather than absorbed. The result is a table of rows, so the same function backs
both the bootstrap's fail-on-delta assertion and an operator-facing expected-vs-actual report.

Key classes:
- VerificationRow: one check — its label, the expected value, and the value actually found.
- VerificationReport: every row for one verification pass, plus the deltas and an overall verdict.

Key functions:
- count_configured_actions: how many alert actions the configured targets require (derived).
- verify_story: re-read live state and compare it with the configured expectations.
- format_deltas: join every delta into one PHI-free message naming them all.

Notes:
- Expected/actual are compared as STRINGS so one uniform equality decides every row and the report
  renders directly as a two-column table (Phase 10c) with no per-check formatting rules.
- Counts are read with `SELECT count(*)` filtered by `agency_id`, so verification is tenant-scoped
  like every other read and can never see another tenant's rows.
- No transaction payload, account, credential, or review note appears in a row: labels are
  scenario ids and enum values, values are counts and enum values.
- The training-label expectation is zero: the bootstrap resolves its configured alert without a
  training label, because an outcome label is a human fraud judgement the story does not declare
  (the same reason no band is ever written directly). A visitor resolving an alert therefore shows
  up as a delta here — which is the point; `--reset` restores the baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Alert,
    AlertAction,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    AuditLog,
    ModelInferenceLog,
    RunStatus,
    SarDraft,
    SarStatus,
    TrainingLabel,
    Transaction,
)
from fraudlens_backend.db.repositories import ModelRegistryRepository
from fraudlens_backend.portfolio_demo.config import PortfolioDemoConfig, PortfolioDemoScenario
from fraudlens_core import RiskBand

# The label the dashboard and this report use for `risk_band IS NULL` (never a band value).
UNSCORED_LABEL = "unscored"

# Alert statuses the pipeline's own alert-raise produces, so a target naming one needs no action:
# `open` for a threshold-crossing row, `pending_review` when a force-review flag was computed.
PIPELINE_RAISED_STATUSES: frozenset[AlertStatus] = frozenset(
    {AlertStatus.OPEN, AlertStatus.PENDING_REVIEW}
)

# SAR statuses only a human review DECISION reaches (the pipeline drafts `draft` or `failed`).
DECIDED_SAR_STATES: frozenset[SarStatus] = frozenset({SarStatus.APPROVED, SarStatus.REJECTED})

_PRESENT = "present"
_MISSING = "missing"
_NONE = "none"


class VerificationRow(BaseModel):
    """One verification check: what was expected and what the database actually holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: str = Field(..., min_length=1, description="PHI-free label identifying the check.")
    expected: str = Field(..., description="Value the configured story declares.")
    actual: str = Field(..., description="Value re-read from the database.")

    @property
    def passed(self) -> bool:
        """True when the live value equals the configured expectation."""
        return self.expected == self.actual

    def describe(self) -> str:
        """Return the PHI-free one-line delta description used in failure messages."""
        return f"{self.check}: expected {self.expected}, found {self.actual}"


class VerificationReport(BaseModel):
    """The full expected-vs-actual table for one verification pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_version: str = Field(..., description="Story revision the expectations were read from.")
    rows: tuple[VerificationRow, ...] = Field(..., description="Every check, in report order.")

    @property
    def deltas(self) -> tuple[VerificationRow, ...]:
        """Return only the rows whose live value differs from the configured expectation."""
        return tuple(row for row in self.rows if not row.passed)

    @property
    def ok(self) -> bool:
        """True when every check passed."""
        return not self.deltas

    def render(self) -> str:
        """Render the report as an aligned expected/actual/verdict table (operator-facing)."""
        width = max((len(row.check) for row in self.rows), default=len("check"))
        header = f"{'check'.ljust(width)}  {'expected':>12}  {'actual':>12}  verdict"
        lines = [header, "-" * len(header)]
        for row in self.rows:
            verdict = "PASS" if row.passed else "FAIL"
            lines.append(
                f"{row.check.ljust(width)}  {row.expected:>12}  {row.actual:>12}  {verdict}"
            )
        return "\n".join(lines)


async def _count(session: AsyncSession, model: type[object], *filters: object) -> int:
    """Return `SELECT count(*)` for a model under the supplied (already tenant-scoped) filters."""
    stmt = select(func.count()).select_from(model).where(*filters)  # type: ignore[arg-type]
    return int((await session.execute(stmt)).scalar_one())


async def _transaction_rows(
    session: AsyncSession, config: PortfolioDemoConfig
) -> list[VerificationRow]:
    """Check the transaction total, the scored/unscored split, and the per-band distribution."""
    agency_id = config.agency.id
    scope = Transaction.agency_id == agency_id
    total = await _count(session, Transaction, scope)
    unscored = await _count(session, Transaction, scope, Transaction.risk_band.is_(None))
    rows = [
        VerificationRow(
            check="transactions.total",
            expected=str(config.expected.transactions),
            actual=str(total),
        ),
        VerificationRow(
            check=f"transactions.{UNSCORED_LABEL}",
            expected=str(config.expected.unscored),
            actual=str(unscored),
        ),
    ]
    for band in RiskBand:
        found = await _count(session, Transaction, scope, Transaction.risk_band == band)
        rows.append(
            VerificationRow(
                check=f"transactions.riskBand.{band.value}",
                expected=str(config.expected.risk_bands.get(band, 0)),
                actual=str(found),
            )
        )
    return rows


async def _alert_and_sar_rows(
    session: AsyncSession, config: PortfolioDemoConfig
) -> list[VerificationRow]:
    """Check every alert status and SAR status count (statuses absent from config expect zero)."""
    agency_id = config.agency.id
    rows: list[VerificationRow] = []
    for status in AlertStatus:
        found = await _count(session, Alert, Alert.agency_id == agency_id, Alert.status == status)
        rows.append(
            VerificationRow(
                check=f"alerts.{status.value}",
                expected=str(config.expected.alert_states.get(status, 0)),
                actual=str(found),
            )
        )
    for sar_status in SarStatus:
        found = await _count(
            session, SarDraft, SarDraft.agency_id == agency_id, SarDraft.status == sar_status
        )
        rows.append(
            VerificationRow(
                check=f"sar.{sar_status.value}",
                expected=str(config.expected.sar_states.get(sar_status, 0)),
                actual=str(found),
            )
        )
    return rows


async def _scenario_result(
    session: AsyncSession, config: PortfolioDemoConfig, scenario: PortfolioDemoScenario
) -> AnalysisResult | None:
    """Return the immutable result snapshot for a scenario's latest run, or None when unscored."""
    stmt = (
        select(AnalysisResult)
        .join(AnalysisRun, AnalysisResult.run_id == AnalysisRun.id)
        .join(Transaction, AnalysisRun.transaction_id == Transaction.id)
        .where(
            AnalysisResult.agency_id == config.agency.id,
            Transaction.agency_id == config.agency.id,
            Transaction.external_id == config.external_id(scenario),
        )
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _fired_codes(result: AnalysisResult) -> str:
    """Return the sorted, comma-joined rule codes a persisted result snapshot recorded."""
    codes = sorted(
        str(hit["code"])
        for hit in (result.rule_hits or [])
        if isinstance(hit, dict) and "code" in hit
    )
    return ",".join(codes) or _NONE


def _expected_codes(scenario: PortfolioDemoScenario) -> str:
    """Return the sorted, comma-joined rule codes the story pins for a scenario."""
    return ",".join(sorted(scenario.expected_triggered_rules)) or _NONE


async def _scenario_rows(
    session: AsyncSession, config: PortfolioDemoConfig
) -> list[VerificationRow]:
    """Check each scored scenario's persisted band and fired rule codes against the story."""
    rows: list[VerificationRow] = []
    for scenario in config.scored_scenarios:
        result = await _scenario_result(session, config, scenario)
        band = _MISSING if result is None else result.risk_band.value
        codes = _MISSING if result is None else _fired_codes(result)
        expected_band = (
            scenario.expected_risk_band.value if scenario.expected_risk_band is not None else _NONE
        )
        rows.append(
            VerificationRow(
                check=f"scenario.{scenario.scenario_id}.band", expected=expected_band, actual=band
            )
        )
        rows.append(
            VerificationRow(
                check=f"scenario.{scenario.scenario_id}.rules",
                expected=_expected_codes(scenario),
                actual=codes,
            )
        )
    return rows


async def _provenance_rows(
    session: AsyncSession, config: PortfolioDemoConfig
) -> list[VerificationRow]:
    """Check the active model label plus the run/result/inference/event provenance of each run."""
    agency_id = config.agency.id
    scored = len(config.scored_scenarios)
    pointer = await ModelRegistryRepository(session).build_pointer()
    active = _MISSING if pointer is None else pointer.active_version_label
    completed = await _count(
        session,
        AnalysisRun,
        AnalysisRun.agency_id == agency_id,
        AnalysisRun.status == RunStatus.COMPLETED,
    )
    scored_by_model = await _count(
        session,
        AnalysisRun,
        AnalysisRun.agency_id == agency_id,
        AnalysisRun.model_version == config.model.version_label,
    )
    runs_with_events = len(
        (
            await session.execute(
                select(AnalysisRunEvent.run_id)
                .where(AnalysisRunEvent.agency_id == agency_id)
                .group_by(AnalysisRunEvent.run_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        VerificationRow(
            check="model.activeVersionLabel",
            expected=config.model.version_label,
            actual=active,
        ),
        VerificationRow(check="runs.completed", expected=str(scored), actual=str(completed)),
        VerificationRow(
            check="runs.onPinnedModel", expected=str(scored), actual=str(scored_by_model)
        ),
        VerificationRow(
            check="runs.results",
            expected=str(scored),
            actual=str(
                await _count(session, AnalysisResult, AnalysisResult.agency_id == agency_id)
            ),
        ),
        VerificationRow(
            check="runs.inferenceLogs",
            expected=str(scored),
            actual=str(
                await _count(session, ModelInferenceLog, ModelInferenceLog.agency_id == agency_id)
            ),
        ),
        VerificationRow(
            check="runs.withEvents", expected=str(scored), actual=str(runs_with_events)
        ),
    ]


async def _history_rows(
    session: AsyncSession, config: PortfolioDemoConfig, *, expected_actions: int
) -> list[VerificationRow]:
    """Check the action trail, the story-correlated audit trail, and the training-label history."""
    agency_id = config.agency.id
    actions = await _count(session, AlertAction, AlertAction.agency_id == agency_id)
    audited = await _count(
        session,
        AuditLog,
        AuditLog.agency_id == agency_id,
        AuditLog.request_id == config.audit_request_id,
    )
    labels = await _count(session, TrainingLabel, TrainingLabel.agency_id == agency_id)
    return [
        VerificationRow(
            check="alertActions.total", expected=str(expected_actions), actual=str(actions)
        ),
        VerificationRow(
            check="auditLogs.storyCorrelated",
            expected=_PRESENT,
            actual=_PRESENT if audited else _MISSING,
        ),
        VerificationRow(check="trainingLabels.total", expected="0", actual=str(labels)),
    ]


def count_configured_actions(config: PortfolioDemoConfig) -> int:
    """Return how many `alert_actions` rows the configured targets produce (PHI-free, derived).

    Two sources, both derived from the story rather than counted by hand:
      * one STATUS transition per alert whose target is not a state the pipeline already raises it
        in (`open` for a high row, `pending_review` for a critical one are no-ops by construction);
      * one `comment` per SAR decision the bootstrap applies, carrying the configured approval or
        rejection note — the SAR review path persists no free text of its own, and `comment` never
        changes status so it cannot move a pinned `alert_states` count.
    """
    transitions = sum(
        1
        for scenario in config.scenarios
        if scenario.alert_target is not None
        and scenario.alert_target not in PIPELINE_RAISED_STATUSES
    )
    decisions = sum(1 for scenario in config.scenarios if scenario.sar_target in DECIDED_SAR_STATES)
    return transitions + decisions


async def verify_story(session: AsyncSession, config: PortfolioDemoConfig) -> VerificationReport:
    """Re-read the tenant's live state and compare every configured expectation against it."""
    rows: list[VerificationRow] = []
    rows.extend(await _transaction_rows(session, config))
    rows.extend(await _alert_and_sar_rows(session, config))
    rows.extend(await _scenario_rows(session, config))
    rows.extend(await _provenance_rows(session, config))
    rows.extend(
        await _history_rows(session, config, expected_actions=count_configured_actions(config))
    )
    return VerificationReport(story_version=config.story_version, rows=tuple(rows))


def format_deltas(deltas: Sequence[VerificationRow]) -> str:
    """Join every delta into one PHI-free message so a failure names them all at once."""
    return "; ".join(row.describe() for row in deltas)
