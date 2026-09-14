"""Summary: Secret-loading remote entrypoint for the RunPod vLLM benchmark harness.

Key classes:
- (none)

Key functions:
- main: load the mode-0600 vLLM token, pin process runtime provenance, and dispatch the harness.

Notes:
- The token is read only from the encrypted Pod volume and is never printed or passed in argv.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path

import benchmark_vllm
from lib.runpod_gpu.config import DEFAULT_CONFIG as DEFAULT_RUNPOD_CONFIG
from lib.runpod_gpu.config import load_config as load_runpod_config
from lib.vllm_bench.config import DEFAULT_VLLM_BENCH_CONFIG
from lib.vllm_bench.config import load_config as load_vllm_config


def main(argv: Sequence[str] | None = None) -> int:
    """Inject runtime-only values and dispatch an ordinary benchmark command."""
    runpod = load_runpod_config(DEFAULT_RUNPOD_CONFIG)
    vllm = load_vllm_config(DEFAULT_VLLM_BENCH_CONFIG)
    token_path = Path(runpod.remote.api_key_path)
    if stat.S_IMODE(token_path.stat().st_mode) != stat.S_IRUSR | stat.S_IWUSR:
        raise ValueError("vLLM API key file must have mode 0600")
    token = token_path.read_text(encoding="utf-8")
    if not token.strip():
        raise ValueError("vLLM API key file is empty")
    os.environ[vllm.server.api_key_env] = token
    os.environ[vllm.server.image_digest_env] = runpod.pod.image_digest
    os.environ["VLLM_BENCH_RUNTIME"] = "process"
    return benchmark_vllm.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
