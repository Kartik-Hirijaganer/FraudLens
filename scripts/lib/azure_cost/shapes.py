"""Summary: Reads the deployment shapes the cost model prices out of the committed Terraform
sources rather than restating them in config. Replicas, region, vCPU, memory, the Log Analytics
daily cap, and the AKS node SKUs and counts each have exactly one home in `infra/terraform`, so a
Terraform edit moves the projection with no second value to keep in step (rule 5).

Key classes:
- DeploymentShapes: every committed shape the projection depends on.
- ShapeError: safe failure when a required assignment is absent or malformed.

Key functions:
- read_assignment: return one HCL `key = value` right-hand side with comments stripped.
- load_shapes: assemble DeploymentShapes from the configured Terraform sources.

Notes:
- The reader is deliberately a regex over the committed source, not a `terraform` invocation:
  it must work with no Azure credentials, no provider download, and no backend.
- Memory is declared as an HCL string (`"1Gi"`); it is normalized to GiB here so the retail
  GiB-second meters apply directly.
- `revision_mode` is read because `max_replicas` is a PER-REVISION limit. Under `Multiple` the
  app-level replica count is the per-revision limit times the number of revisions holding
  replicas at once, so the mode decides whether `max_replicas` bounds the app or only one
  revision of it. Reading it here keeps that distinction in the model instead of in a comment.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.azure_cost.config import CostModelConfig

_MEMORY_RE = re.compile(r"^(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>Gi|Mi)$")
_MIB_PER_GIB = Decimal("1024")


class ShapeError(RuntimeError):
    """Raised when a committed Terraform source lacks a shape the model needs."""


class DeploymentShapes(BaseModel):
    """The committed deployment shapes the projection is derived from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aca_region: str = Field(..., min_length=1, description="Container Apps region.")
    aca_min_replicas: int = Field(..., ge=0, description="Committed minimum gateway replicas.")
    aca_max_replicas: int = Field(
        ..., gt=0, description="Committed maximum replicas PER REVISION, not per app."
    )
    aca_revision_mode: str = Field(
        ..., min_length=1, description="Container Apps revision mode (Single or Multiple)."
    )
    aca_vcpu: Decimal = Field(..., gt=0, description="vCPU allocated per gateway replica.")
    aca_memory_gib: Decimal = Field(..., gt=0, description="Memory GiB allocated per replica.")
    log_daily_quota_gb: Decimal = Field(..., gt=0, description="Log Analytics daily ingestion cap.")
    aks_region: str = Field(..., min_length=1, description="AKS demonstration region.")
    aks_system_vm_size: str = Field(..., min_length=1, description="AKS system pool VM SKU.")
    aks_user_vm_size: str = Field(..., min_length=1, description="AKS user pool VM SKU.")
    aks_user_min_count: int = Field(..., ge=0, description="Baseline user-pool node count.")
    aks_user_max_count: int = Field(..., gt=0, description="Maximum user-pool node count.")


def read_assignment(source: Path, key: str) -> str:
    """Return the right-hand side of one HCL `key = value` assignment."""
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise ShapeError(f"terraform source is unreadable: {source}") from error
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*(?:#.*)?$", text, re.MULTILINE)
    if match is None:
        raise ShapeError(f"{source}: {key} is not assigned")
    return match.group(1).strip()


def _quoted(source: Path, key: str) -> str:
    value = read_assignment(source, key).strip('"')
    if not value:
        raise ShapeError(f"{source}: {key} is blank")
    return value


def _integer(source: Path, key: str) -> int:
    raw = read_assignment(source, key)
    try:
        return int(raw)
    except ValueError as error:
        raise ShapeError(f"{source}: {key} is not an integer ({raw})") from error


def _decimal(source: Path, key: str) -> Decimal:
    raw = read_assignment(source, key)
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise ShapeError(f"{source}: {key} is not a number ({raw})") from error


def _default_decimal(source: Path, variable: str) -> Decimal:
    """Return a module variable's committed `default`, the value every root inherits."""
    text = source.read_text(encoding="utf-8")
    blocks = text.split(f'variable "{variable}"')
    if len(blocks) < 2:  # noqa: PLR2004 - split yields [before, after] only when the block exists
        raise ShapeError(f"{source}: variable {variable} is not declared")
    match = re.search(r"^\s*default\s*=\s*(.+?)\s*(?:#.*)?$", blocks[1], re.MULTILINE)
    if match is None:
        raise ShapeError(f"{source}: variable {variable} has no default")
    raw = match.group(1).strip().strip('"')
    memory = _MEMORY_RE.match(raw)
    if memory is not None:
        amount = Decimal(memory.group("amount"))
        return amount if memory.group("unit") == "Gi" else amount / _MIB_PER_GIB
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise ShapeError(
            f"{source}: variable {variable} default is not a number ({raw})"
        ) from error


def load_shapes(config: CostModelConfig, repo_root: Path) -> DeploymentShapes:
    """Assemble every committed shape the projection depends on."""
    aca = repo_root / config.shapes.aca_tfvars
    gateway = repo_root / config.shapes.gateway_module
    observability = repo_root / config.shapes.observability_module
    aks = repo_root / config.shapes.aks_tfvars
    return DeploymentShapes(
        aca_region=_quoted(aca, "location"),
        aca_min_replicas=_integer(aca, "min_replicas"),
        aca_max_replicas=_integer(aca, "max_replicas"),
        aca_revision_mode=_quoted(gateway, "revision_mode"),
        aca_vcpu=_default_decimal(gateway, "cpu"),
        aca_memory_gib=_default_decimal(gateway, "memory"),
        log_daily_quota_gb=_decimal(observability, "daily_quota_gb"),
        aks_region=_quoted(aks, "location"),
        aks_system_vm_size=_quoted(aks, "system_vm_size"),
        aks_user_vm_size=_quoted(aks, "user_vm_size"),
        aks_user_min_count=_integer(aks, "user_min_count"),
        aks_user_max_count=_integer(aks, "user_max_count"),
    )
