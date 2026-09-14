"""Summary: Deterministic PostgreSQL barrier for the local worker-crash durability proof.

Key classes:
- (none)

Key functions:
- hold_transaction_reads: block worker transaction reads until the lease-owning pod is killed.

Notes:
- The barrier touches only the disposable kind PostgreSQL database and is always released.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

from lib.k8s_demo.config import REPO_ROOT, K8sDemoConfig
from lib.k8s_demo.kubectl import CommandError, Kubectl

_LOCK_APPLICATION = "fraudlens-k8s-durability-lock"
_PROCESS_STOP_TIMEOUT_SECONDS = 5


def _psql(config: K8sDemoConfig, sql: str) -> list[str]:
    """Build one in-pod psql command without exposing a credential."""
    return [
        "-n",
        config.namespace,
        "exec",
        "deployment/postgres",
        "-c",
        "postgres",
        "--",
        "psql",
        "-U",
        config.postgres_user,
        "-d",
        config.postgres_database,
        "-tAc",
        sql,
    ]


def _wait_for_lock(
    config: K8sDemoConfig, kubectl: Kubectl, process: subprocess.Popen[bytes]
) -> None:
    """Wait for the background session's granted table lock."""
    deadline = time.monotonic() + config.worker_claim_timeout_seconds
    query = "SELECT count(*) FROM pg_locks"
    query += " WHERE relation = 'transactions'::regclass"
    query += " AND mode = 'AccessExclusiveLock' AND granted"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CommandError("durability database barrier exited before acquiring its lock")
        result = kubectl.run(_psql(config, query), check=False)
        if result.returncode == 0 and result.stdout.strip() == "1":
            return
        time.sleep(config.worker_claim_poll_seconds)
    raise CommandError("durability database barrier did not acquire its lock")


def _release_lock(
    config: K8sDemoConfig, kubectl: Kubectl, process: subprocess.Popen[bytes]
) -> None:
    """Terminate the named lock session and bound local kubectl cleanup."""
    terminate = "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
    terminate += f" WHERE application_name = '{_LOCK_APPLICATION}'"
    kubectl.run(_psql(config, terminate), check=False)
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)


@contextmanager
def hold_transaction_reads(config: K8sDemoConfig, kubectl: Kubectl) -> Iterator[None]:
    """Hold transaction reads so the selected worker remains in-flight during SIGKILL."""
    kubectl.assert_mutation_allowed(platform="kind")
    lock_sql = f"SET application_name = '{_LOCK_APPLICATION}'; BEGIN;"
    lock_sql += " LOCK TABLE transactions IN ACCESS EXCLUSIVE MODE;"
    lock_sql += f" SELECT pg_sleep({config.worker_fault_lock_seconds})"
    process = subprocess.Popen(
        [kubectl.binary, "--context", config.context, *_psql(config, lock_sql)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_lock(config, kubectl, process)
        yield
    finally:
        _release_lock(config, kubectl, process)
