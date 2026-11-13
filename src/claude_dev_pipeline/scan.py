"""Fail the tree on private paths and filled-in account ids."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "runs",
    "dist",
    "build",
    ".codegraph",
    "graphify-out",
}

# Built so this file does not itself contain the forbidden path spellings.
_PRIVATE_RES = (
    ("private-path", re.compile(re.escape("/home") + "/")),
    ("private-path", re.compile(re.escape("/Users") + "/")),
    ("private-path", re.compile("C:" + "\\\\")),
    ("private-path", re.compile(r"[A-Za-z]:\\Users\\")),
)
_ACCOUNT_ID = re.compile(
    r"(?i)\b(?:organization_id|workspace_id|org_id)\b\s*[:=]\s*['\"]?[A-Za-z0-9][A-Za-z0-9_-]{7,}"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


def load_allowlist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    entries = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.add(line)
    return entries


def scan_tree(root: Path, allowlist: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in allowlist:
            continue
        findings.extend(_scan_file(path, relative))
    return findings


def _scan_file(path: Path, relative: str) -> list[Finding]:
    data = path.read_bytes()
    if b"\0" in data[:1024] or len(data) > 2_000_000:
        return []
    text = data.decode("utf-8", errors="replace")
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for _, pattern in _PRIVATE_RES):
            findings.append(Finding(relative, line_no, "private-path"))
            continue
        if _ACCOUNT_ID.search(line):
            findings.append(Finding(relative, line_no, "account-id"))
    return findings
