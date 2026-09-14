"""Summary: Read-only Kubernetes demo cleanup verification separated from lifecycle mutation.

Key classes:
- (none)

Key functions:
- verify_kind_clean: prove that no configured kind cluster or backing Docker nodes remain.

Notes:
- This module never deletes anything; it is safe to run after successful or failed demos.
"""

from __future__ import annotations

from lib.k8s_demo.config import K8sDemoConfig
from lib.k8s_demo.kind import KindOperator
from lib.k8s_demo.kubectl import CommandError, CommandRunner, run_command


def verify_kind_clean(config: K8sDemoConfig, *, runner: CommandRunner = run_command) -> None:
    """Assert the cluster name and its Docker node labels are both absent."""
    operator = KindOperator(config, runner=runner)
    if config.cluster_name in operator.clusters():
        raise CommandError("kind cleanup verification found the configured cluster")
    operator.verify_clean()
