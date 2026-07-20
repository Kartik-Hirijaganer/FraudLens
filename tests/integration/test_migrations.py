"""Migration tests (plan §17.1 "DB migration"): the Alembic migration applies and reverses
on a temp DB, the upgraded schema matches the ORM metadata exactly (no drift), and there is
exactly one head. These are sync tests so Alembic's command API can drive its own event loop
(env.py runs migrations over an async engine)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from fraudlens_backend.db.models import Base

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(db_url: str) -> Config:
    """Build an Alembic config pointed at the repo's migrations and a given DB URL."""
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _table_names(db_path: Path) -> set[str]:
    """Return the table names present in the SQLite database at db_path."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_every_model_table(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade.db"
    command.upgrade(_config(f"sqlite+aiosqlite:///{db_path}"), "head")
    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert _table_names(db_path) == expected


def test_upgrade_columns_match_models(tmp_path: Path) -> None:
    # Stronger than table names: every table's columns in the migrated schema must equal
    # the ORM model's columns (guards against migration/model drift, e.g. a dropped column).
    db_path = tmp_path / "columns.db"
    command.upgrade(_config(f"sqlite+aiosqlite:///{db_path}"), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        for name, table in Base.metadata.tables.items():
            migrated = {col["name"] for col in inspector.get_columns(name)}
            assert migrated == set(table.columns.keys()), name
    finally:
        engine.dispose()


def test_downgrade_drops_every_model_table(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    # Only Alembic's own bookkeeping table may remain after a full downgrade.
    assert _table_names(db_path) <= {"alembic_version"}


def test_alert_origin_migration_backfills_and_downgrades(tmp_path: Path) -> None:
    """Existing alerts become pipeline rows; downgrade removes the provenance column."""
    db_path = tmp_path / "alert-origin.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(cfg, "0002_extend_alert_statuses")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO alerts
                    (transaction_id, run_id, status, severity, assigned_to, review_flags,
                     agency_id, id)
                    VALUES
                    (:transaction_id, :run_id, 'open', 'high', NULL, '[]', :agency_id, :id)"""
                ),
                {
                    "transaction_id": "11111111111141118111111111111111",
                    "run_id": "22222222222242228222222222222222",
                    "agency_id": "33333333333343338333333333333333",
                    "id": "44444444444444448444444444444444",
                },
            )
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT origin FROM alerts")).scalar_one() == "pipeline"
        origin = next(
            column for column in inspect(engine).get_columns("alerts") if column["name"] == "origin"
        )
        assert origin["nullable"] is False
        assert "pipeline" in str(origin["default"])
        assert any(
            "origin" in str(constraint["sqltext"])
            and "pipeline" in str(constraint["sqltext"])
            and "seed" in str(constraint["sqltext"])
            for constraint in inspect(engine).get_check_constraints("alerts")
        )
        command.downgrade(cfg, "0002_extend_alert_statuses")
        assert "origin" not in {column["name"] for column in inspect(engine).get_columns("alerts")}
    finally:
        engine.dispose()


def test_exactly_one_alembic_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite+aiosqlite:///unused.db"))
    assert len(script.get_heads()) == 1
