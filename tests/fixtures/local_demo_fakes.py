"""Shared process fake for local-demo lifecycle tests."""


class _FakeProc:
    """Minimal stand-in for subprocess.Popen exposing poll()/returncode for the smoke gate."""

    def __init__(self, exit_code: int | None) -> None:
        self.returncode = exit_code
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        """Record a graceful stop requested by the orchestrator."""
        self.terminated = True

    def wait(self, *, timeout: int) -> int:
        """Return immediately like an already-cooperative child process."""
        del timeout
        return self.returncode or 0
