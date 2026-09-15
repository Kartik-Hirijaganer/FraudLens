"""Summary: Explicit-confirmation RunPod lifecycle and cleanup behavior tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- No cloud API is called; FakeApi records intended lifecycle transitions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runpod_gpu_fakes import (
    GIT_SHA,
    PUBLIC_KEY,
    RUN_ID,
    FakeApi,
    inventory,
    pod,
    session,
    typed_api,
)

from lib.experiments.budget import load_budget_config
from lib.runpod_gpu.api import NetworkVolume
from lib.runpod_gpu.config import load_config
from lib.runpod_gpu.lifecycle import (
    create_session,
    delete_session,
    start_session,
    stop_session,
    verify_clean,
)
from lib.runpod_gpu.planning import build_plan
from lib.runpod_gpu.session import load_session, write_session


def _plan(config, sandbox, monkeypatch):
    monkeypatch.setattr("lib.runpod_gpu.planning.git_commit", lambda _root: GIT_SHA)
    return build_plan(
        config,
        load_budget_config(Path.cwd()),
        [inventory(config)],
        run_id=RUN_ID,
        repo_root=sandbox,
    )


def test_create_requires_confirmation_and_refuses_duplicate(sandbox, monkeypatch) -> None:
    config = load_config()
    plan = _plan(config, sandbox, monkeypatch)
    api = FakeApi(pod(config), pods=())
    with pytest.raises(PermissionError, match="explicit"):
        create_session(
            config,
            typed_api(api),
            plan,
            public_key=PUBLIC_KEY,
            repo_root=sandbox,
            confirmed=False,
        )
    duplicate = FakeApi(pod(config))
    with pytest.raises(ValueError, match="already exists"):
        create_session(
            config,
            typed_api(duplicate),
            plan,
            public_key=PUBLIC_KEY,
            repo_root=sandbox,
            confirmed=True,
        )


def test_create_persists_identity_and_stops_contract_drift(sandbox, monkeypatch) -> None:
    config = load_config()
    plan = _plan(config, sandbox, monkeypatch)
    api = FakeApi(pod(config), pods=())
    created = create_session(
        config,
        typed_api(api),
        plan,
        public_key=PUBLIC_KEY,
        repo_root=sandbox,
        confirmed=True,
    )
    assert load_session(config, sandbox, RUN_ID) == created
    assert api.calls == [("create", created.pod_id)]

    other_root = sandbox / "other"
    drifted = FakeApi(pod(config, costPerHr="0.75"), pods=())
    with pytest.raises(ValueError, match="hourly rate"):
        create_session(
            config,
            typed_api(drifted),
            plan,
            public_key=PUBLIC_KEY,
            repo_root=other_root,
            confirmed=True,
        )
    assert drifted.calls[-1] == ("stop", drifted.current.pod_id)
    assert load_session(config, other_root, RUN_ID).pod_id == drifted.current.pod_id


def test_start_stop_and_delete_each_require_separate_confirmation(sandbox) -> None:
    config = load_config()
    write_session(config, sandbox, session(config))

    stopped_api = FakeApi(pod(config, desiredStatus="EXITED"))
    with pytest.raises(PermissionError, match="start"):
        start_session(
            config, typed_api(stopped_api), run_id=RUN_ID, repo_root=sandbox, confirmed=False
        )
    start_session(config, typed_api(stopped_api), run_id=RUN_ID, repo_root=sandbox, confirmed=True)
    assert ("start", stopped_api.current.pod_id) in stopped_api.calls

    running_api = FakeApi(pod(config))
    with pytest.raises(PermissionError, match="stop"):
        stop_session(
            config, typed_api(running_api), run_id=RUN_ID, repo_root=sandbox, confirmed=False
        )
    stop_session(config, typed_api(running_api), run_id=RUN_ID, repo_root=sandbox, confirmed=True)
    assert ("stop", running_api.current.pod_id) in running_api.calls

    with pytest.raises(PermissionError, match="deletion"):
        delete_session(
            config, typed_api(stopped_api), run_id=RUN_ID, repo_root=sandbox, confirmed=False
        )
    deleted = delete_session(
        config, typed_api(stopped_api), run_id=RUN_ID, repo_root=sandbox, confirmed=True
    )
    assert deleted.deleted_at is not None
    assert ("delete", stopped_api.current.pod_id) in stopped_api.calls


@pytest.mark.parametrize(
    ("operation", "current", "message"),
    (
        ("start", "RUNNING", "only the matching stopped"),
        ("stop", "TERMINATED", "running or stopped"),
        ("delete", "RUNNING", "must be identity-matched and stopped"),
    ),
)
def test_lifecycle_rejects_wrong_provider_state(
    sandbox, operation: str, current: str, message: str
) -> None:
    config = load_config()
    write_session(config, sandbox, session(config))
    api = FakeApi(pod(config, desiredStatus=current))
    function = {"start": start_session, "stop": stop_session, "delete": delete_session}[operation]
    with pytest.raises(ValueError, match=message):
        function(config, typed_api(api), run_id=RUN_ID, repo_root=sandbox, confirmed=True)


def test_cleanup_evidence_checks_pods_and_independent_volumes() -> None:
    config = load_config()
    clean_api = FakeApi(pod(config), pods=(), volumes=())
    evidence = verify_clean(config, typed_api(clean_api), run_id=RUN_ID)
    assert evidence.clean is True

    volume = NetworkVolume.model_validate(
        {
            "id": "residual-volume",
            "name": f"{config.pod_name(RUN_ID)}-data",
            "size": 50,
            "dataCenterId": "US-TEST-1",
        }
    )
    dirty_api = FakeApi(pod(config), volumes=(volume,))
    with pytest.raises(ValueError, match="cleanup incomplete"):
        verify_clean(config, typed_api(dirty_api), run_id=RUN_ID)
