"""Training CLI facade tests for configuration guards, arguments, and artifact paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import train_model
from fraudlens_backend.settings import AppSettings


async def test_training_refuses_production_before_loading_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(train_model, "get_settings", lambda: AppSettings(environment="prod"))
    assert await train_model._amain("synthetic", False, 100, 7, None, True) == 1


def test_main_forwards_explicit_cli_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[object, ...]] = []

    async def fake_amain(*args: object) -> int:
        received.append(args)
        return 7

    monkeypatch.setattr(train_model, "_amain", fake_amain)
    assert (
        train_model.main(
            [
                "--source",
                "synthetic",
                "--rows",
                "120",
                "--seed",
                "9",
                "--sample-rows",
                "60",
                "--artifact-only",
            ]
        )
        == 7
    )
    assert received == [("synthetic", False, 120, 9, 60, True)]


def test_artifact_paths_and_manifest_sidecar_are_repository_anchored(tmp_path: Path) -> None:
    absolute = tmp_path / "absolute"
    assert train_model.artifacts_root(AppSettings(model_artifacts_dir=str(absolute))) == absolute
    relative = train_model.artifacts_root(AppSettings(model_artifacts_dir="models"))
    assert relative == train_model.REPO_ROOT / "models"

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = train_model._synthetic_manifest(7, 120)
    train_model._write_manifest_sidecar(bundle, manifest, seed=7, rows=120)
    payload = json.loads((bundle / train_model.MANIFEST_SIDECAR).read_text(encoding="utf-8"))
    assert payload["manifest"]["source"] == "synthetic"
    assert payload["seed"] == 7
    assert payload["rows"] == 120
