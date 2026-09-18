"""Summary: Secret-loading remote entrypoint for the RunPod vLLM benchmark harness.

Key classes:
- (none)

Key functions:
- main: load the mode-0600 vLLM token, pin process runtime provenance, and dispatch the harness.

Notes:
- The token is read only from private container-disk storage and is never printed or passed in argv.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import benchmark_vllm
from fraudlens_llm import LlmSettings, load_providers
from lib.runpod_gpu.config import DEFAULT_CONFIG as DEFAULT_RUNPOD_CONFIG
from lib.runpod_gpu.config import load_config as load_runpod_config
from lib.vllm_bench.config import DEFAULT_VLLM_BENCH_CONFIG, VllmBenchConfig
from lib.vllm_bench.config import load_config as load_vllm_config

_GIT_COMMIT_ENV = "VLLM_BENCH_GIT_COMMIT"
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@contextmanager
def _runtime_environment(values: Mapping[str, str]) -> Iterator[None]:
    """Apply runtime values for one dispatch and restore the caller's environment."""
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _scenario_runtime(
    argv: Sequence[str] | None, config: VllmBenchConfig, token: str
) -> dict[str, str]:
    """Bind a single-endpoint scenario to localhost using its declared connection env vars."""
    arguments = tuple(argv or ())
    if "run-scenario" not in arguments or "--scenario" not in arguments:
        return {}
    scenario_index = arguments.index("--scenario") + 1
    if scenario_index >= len(arguments):
        raise ValueError("--scenario requires a value")
    scenario = config.cascade.scenario(arguments[scenario_index])
    registry = load_providers(LlmSettings().providers_path)
    runtime: dict[str, str] = {"FRAUDLENS_ENVIRONMENT": "prod"}
    for role in scenario.endpoints:
        endpoint = config.cascade.endpoints[role]
        connection = registry.connection(endpoint.connection)
        route = registry.route(connection.provider, endpoint.connection)
        if not os.environ.get(route.api_key_env, "").strip():
            runtime[route.api_key_env] = token
        if route.base_url_env is None or os.environ.get(route.base_url_env, "").strip():
            continue
        if len(scenario.endpoints) != 1:
            raise ValueError(f"{route.base_url_env} is required for a multi-endpoint scenario")
        runtime[route.base_url_env] = os.environ.get(
            config.server.base_url_env, str(config.server.base_url)
        )
    return runtime


def main(argv: Sequence[str] | None = None) -> int:
    """Inject runtime-only values and dispatch an ordinary benchmark command."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    runpod = load_runpod_config(DEFAULT_RUNPOD_CONFIG)
    vllm = load_vllm_config(DEFAULT_VLLM_BENCH_CONFIG)
    token_path = Path(runpod.remote.api_key_path)
    if stat.S_IMODE(token_path.stat().st_mode) != stat.S_IRUSR | stat.S_IWUSR:
        raise ValueError("vLLM API key file must have mode 0600")
    token = token_path.read_text(encoding="utf-8")
    if not token.strip():
        raise ValueError("vLLM API key file is empty")
    runtime = {
        vllm.server.api_key_env: token,
        vllm.server.image_digest_env: runpod.pod.image_digest,
        "VLLM_BENCH_RUNTIME": "process",
    }
    runtime.update(_scenario_runtime(arguments, vllm, token))
    commit_path = Path(runpod.remote.git_commit_path)
    if commit_path.is_file():
        commit = commit_path.read_text(encoding="utf-8").strip()
        if _GIT_COMMIT_PATTERN.fullmatch(commit) is None:
            raise ValueError("session Git commit must be 40 lowercase hexadecimal characters")
        runtime[_GIT_COMMIT_ENV] = commit
    elif "run-scenario" in arguments:
        raise ValueError("session Git commit is required for a remote scenario run")
    with _runtime_environment(runtime):
        return benchmark_vllm.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
