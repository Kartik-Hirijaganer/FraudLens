"""Summary: RunPod admission, request rendering, session identity, and SSH tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Git, filesystem identities, and provider responses are fully synthetic.
"""

from __future__ import annotations

from decimal import Decimal
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
from lib.runpod_gpu.config import load_config
from lib.runpod_gpu.planning import build_create_request, build_plan, git_commit, read_public_key
from lib.runpod_gpu.session import (
    load_session,
    matching_pods,
    pod_status,
    scp_argv,
    ssh_argv,
    validate_pod_contract,
    write_session,
)


def _plan(sandbox, monkeypatch, **inventory_changes):
    config = load_config()
    budget = load_budget_config(Path.cwd())
    monkeypatch.setattr("lib.runpod_gpu.planning.git_commit", lambda _root: GIT_SHA)
    return config, build_plan(
        config,
        budget,
        [inventory(config, **inventory_changes)],
        run_id=RUN_ID,
        repo_root=sandbox,
    )


def test_plan_admits_eight_hour_secure_cloud_envelope(sandbox, monkeypatch) -> None:
    config, plan = _plan(sandbox, monkeypatch)
    assert plan.admitted is True
    assert plan.projected_cost_usd == Decimal("5.92000000")
    assert plan.cost_with_margin_usd == Decimal("7.6960000000")
    assert plan.allocation_usd == Decimal("10.00")
    assert plan.gpu_memory_gb == 24
    assert plan.pod_name == config.pod_name(RUN_ID)


def test_create_request_is_ssh_only_secret_free_and_self_stopping(sandbox, monkeypatch) -> None:
    config, plan = _plan(sandbox, monkeypatch)
    request = build_create_request(config, plan, public_key=PUBLIC_KEY)
    payload = request.model_dump(mode="json", by_alias=True)
    assert payload["cloudType"] == "SECURE"
    assert payload["gpuTypeIds"] == [config.pod.gpu_id]
    assert payload["ports"] == ["22/tcp"]
    assert payload["volumeEncrypted"] is True
    assert payload["imageName"] == config.pod.image_reference
    assert "28800" in payload["dockerStartCmd"][0]
    assert "runpodctl pod stop" in payload["dockerStartCmd"][0]
    assert "uv==0.11.3" in payload["dockerStartCmd"][0]
    assert set(payload["env"]) == {"PUBLIC_KEY", "SSH_PUBLIC_KEY", "HF_HOME", "VLLM_CACHE_ROOT"}
    assert "VLLM_API_KEY" not in payload["env"]
    assert "RUNPOD_API_KEY" not in payload["env"]


def test_plan_and_request_fail_closed_on_capacity_quote_or_inventory_drift(
    sandbox, monkeypatch
) -> None:
    config, unavailable = _plan(sandbox, monkeypatch, available=False)
    with pytest.raises(ValueError, match="admitted with current"):
        build_create_request(config, unavailable, public_key=PUBLIC_KEY)
    with pytest.raises(ValueError, match="valid SSH public"):
        build_create_request(
            config, unavailable.model_copy(update={"available": True}), public_key="x"
        )

    budget = load_budget_config(Path.cwd())
    monkeypatch.setattr("lib.runpod_gpu.planning.git_commit", lambda _root: GIT_SHA)
    with pytest.raises(ValueError, match="exactly one"):
        build_plan(config, budget, [], run_id=RUN_ID, repo_root=sandbox)
    drifted = budget.model_copy(
        update={
            "rates": {
                **budget.rates,
                config.rate_key: budget.rates[config.rate_key].model_copy(
                    update={"region": "community-cloud"}
                ),
            }
        }
    )
    with pytest.raises(ValueError, match="do not match"):
        build_plan(config, drifted, [inventory(config)], run_id=RUN_ID, repo_root=sandbox)


def test_git_commit_rejects_dirty_or_invalid_revision(sandbox, monkeypatch) -> None:
    outputs = iter([" M changed.py"])
    monkeypatch.setattr(
        "lib.runpod_gpu.planning._command_output", lambda *_args, **_kwargs: next(outputs)
    )
    with pytest.raises(ValueError, match="clean committed"):
        git_commit(sandbox)

    outputs = iter(["", "not-a-sha"])
    monkeypatch.setattr(
        "lib.runpod_gpu.planning._command_output", lambda *_args, **_kwargs: next(outputs)
    )
    with pytest.raises(ValueError, match="immutable Git"):
        git_commit(sandbox)


def test_public_key_path_is_environment_backed_and_single_line(sandbox, monkeypatch) -> None:
    config = load_config()
    key_path = sandbox / "operator.pub"
    key_path.write_text(PUBLIC_KEY)
    monkeypatch.setenv(config.ssh.public_key_path_env, str(key_path))
    assert read_public_key(config) == PUBLIC_KEY
    key_path.write_text(PUBLIC_KEY + "\n" + PUBLIC_KEY)
    with pytest.raises(ValueError, match="exactly one"):
        read_public_key(config)
    monkeypatch.delenv(config.ssh.public_key_path_env)
    with pytest.raises(ValueError, match="is required"):
        read_public_key(config)


def test_session_round_trip_status_and_ssh_commands(sandbox, monkeypatch) -> None:
    config = load_config()
    current = pod(config)
    api = FakeApi(current)
    expected = write_session(config, sandbox, session(config))
    assert load_session(config, sandbox, RUN_ID) == expected
    assert matching_pods(typed_api(api), current.name) == (current,)
    status = pod_status(config, typed_api(api), run_id=RUN_ID, repo_root=sandbox)
    assert status.public_ip == "192.0.2.10"
    assert status.ssh_port == 22022

    private = sandbox / "operator-key"
    private.write_text("not-read-by-operator")
    monkeypatch.setenv(config.ssh.private_key_path_env, str(private))
    ssh = ssh_argv(config, status)
    assert ssh[0] == "ssh" and str(private) in ssh
    scp = scp_argv(config, status)
    assert scp[0] == "scp" and "22022" in scp


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"name": "wrong"}, "name"),
        ({"gpu": {"id": "wrong", "count": 1}}, "GPU"),
        ({"image": "wrong"}, "image"),
        ({"interruptible": True}, "lifecycle"),
        ({"costPerHr": "0.75"}, "hourly rate"),
        ({"volumeEncrypted": False}, "encrypted volume"),
        ({"ports": ["22/tcp", "8000/http"]}, "network/storage"),
        ({"machine": {"secureCloud": False}}, "Secure Cloud"),
    ),
)
def test_pod_contract_reports_every_frozen_dimension(changes, message: str) -> None:
    config = load_config()
    with pytest.raises(ValueError, match=message):
        validate_pod_contract(
            config,
            pod(config, **changes),
            pod_name=config.pod_name(RUN_ID),
            expected_rate=Decimal("0.740000"),
        )


def test_pod_contract_accepts_unreported_descriptive_fields() -> None:
    """Provider omissions are unknown observations; explicit conflicts still fail above."""
    config = load_config()
    current = pod(config).model_copy(
        update={"gpu": None, "image": None, "interruptible": None, "locked": None}
    )
    validate_pod_contract(
        config,
        current,
        pod_name=config.pod_name(RUN_ID),
        expected_rate=Decimal("0.740000"),
    )


def test_ssh_refuses_unready_pod_or_missing_key(sandbox, monkeypatch) -> None:
    config = load_config()
    api = FakeApi(pod(config, desiredStatus="EXITED", publicIp=None, portMappings={}))
    write_session(config, sandbox, session(config))
    status = pod_status(config, typed_api(api), run_id=RUN_ID, repo_root=sandbox)
    with pytest.raises(ValueError, match="not ready"):
        ssh_argv(config, status)
    ready = status.model_copy(
        update={"desired_status": "RUNNING", "public_ip": "192.0.2.10", "ssh_port": 22022}
    )
    monkeypatch.delenv(config.ssh.private_key_path_env, raising=False)
    with pytest.raises(ValueError, match="is required"):
        ssh_argv(config, ready)
