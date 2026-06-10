"""Summary: Supplementary, config-focused secret guard (rule 4). gitleaks is the
primary repo-wide scanner; this adds a targeted assertion that the layered config
files (config/*.yaml) carry NO inline secrets. Secret-named keys (password, secret,
token, api_key, private_key, access_key, client_secret) must be absent or hold only
an empty/placeholder/Akeyless-reference value — real credentials are fetched from
Akeyless at runtime and never committed.

Key classes:
- (none)

Key functions:
- main: scan config files for inline secrets and return a process exit code.

Notes:
- Deliberately conservative and config-scoped; broad detection is gitleaks' job.
"""

from __future__ import annotations

import re
from pathlib import Path

_SECRET_KEY_RE = re.compile(
    r"(?i)\b(passwd|password|secret|token|api[_-]?key|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|credential)\b"
)
_PLACEHOLDER_RE = re.compile(
    r"^(|null|~|''|\"\"|<.*>|\$\{.*\}|changeme|example.*|akeyless.*)$", re.IGNORECASE
)


def _scan_yaml(path: Path) -> list[str]:
    """Return findings for secret-named keys that carry a non-placeholder inline value."""
    findings: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if _SECRET_KEY_RE.search(key):
            cleaned = value.strip().strip("\"'")
            if not _PLACEHOLDER_RE.match(cleaned):
                name = key.strip()
                findings.append(f"{path}:{lineno}: inline value for secret-like key '{name}'")
    return findings


def main() -> int:
    """Scan config/*.yaml for inline secrets; return 1 if any are found, else 0."""
    repo_root = Path(__file__).resolve().parents[1]
    config_dir = repo_root / "config"
    findings: list[str] = []
    if config_dir.exists():
        for path in sorted(config_dir.rglob("*.y*ml")):
            findings.extend(_scan_yaml(path))
    for finding in findings:
        print(finding)
    if findings:
        print(f"\ncheck_no_secrets FAILED: {len(findings)} inline secret(s) in config")
        return 1
    print("check_no_secrets OK: no inline secrets found in config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
