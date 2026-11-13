"""Dependency-bump lane. One ecosystem, no fast lane, majors get a full review."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")
_MAJOR = re.compile(r"(\d+)\.\d+")
_ADVISORY = re.compile(r"advisory|CVE-\d|GHSA-|vulnerability", re.IGNORECASE)
_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[\w.-]+)")

_ECOSYSTEMS = {
    "npm": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "python": [
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
    ],
    "go": ["go.mod", "go.sum"],
    "rust": ["Cargo.toml", "Cargo.lock"],
    "ruby": ["Gemfile", "Gemfile.lock"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
}


def enabled(config: dict) -> bool:
    return _section(config).get("enabled", True) is not False


def table(config: dict) -> dict[str, list[str]]:
    custom = _section(config).get("ecosystems")
    if isinstance(custom, dict) and custom:
        return {str(key): [str(item) for item in value] for key, value in custom.items()}
    return {key: list(value) for key, value in _ECOSYSTEMS.items()}


def ecosystem_of(path: str, config: dict) -> str:
    name = Path(path).name
    for ecosystem, names in table(config).items():
        if name in names or path in names:
            return ecosystem
    return ""


def planned_paths(issue: dict) -> list[str]:
    text = f"{issue.get('title') or ''}\n{issue.get('body') or ''}"
    found: list[str] = []
    for match in _PATH.findall(text):
        cleaned = match.rstrip(".,:;)")
        if not cleaned or re.fullmatch(r"\d+(?:\.\d+)+", Path(cleaned).name):
            continue
        if cleaned not in found:
            found.append(cleaned)
    return found


def classify_paths(paths: list[str], config: dict) -> dict:
    """Deps lane only when every path belongs to a single ecosystem."""
    if not enabled(config) or not paths:
        return {"lane": ""}
    ecosystems = set()
    for path in paths:
        ecosystem = ecosystem_of(path, config)
        if not ecosystem:
            return {"lane": ""}
        ecosystems.add(ecosystem)
    if len(ecosystems) > 1:
        return {"lane": "", "split": True, "ecosystems": sorted(ecosystems)}
    return {"lane": "deps", "ecosystem": next(iter(ecosystems)), "files": list(paths)}


def is_major(old: str, new: str) -> bool:
    old_majors = [int(item) for item in _MAJOR.findall(old)]
    new_majors = [int(item) for item in _MAJOR.findall(new)]
    for before, after in zip(old_majors, new_majors, strict=False):
        if after > before:
            return True
    return False


def beyond_issue(old: str, new: str, issue_text: str) -> list[str]:
    introduced = set(_VERSION.findall(new)) - set(_VERSION.findall(old))
    return sorted(version for version in introduced if version not in issue_text)


def read_changelog(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def audit(repo: Path, config: dict) -> dict:
    """Read a local audit result. No network."""
    command = str((config.get("checks") or {}).get("audit") or "").strip()
    output = ""
    source = ""
    if command:
        source = command
        completed = subprocess.run(
            shlex.split(command),
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
    elif (repo / "audit-result.txt").is_file():
        source = "audit-result.txt"
        output = (repo / "audit-result.txt").read_text(encoding="utf-8")
    return {
        "source": source,
        "advisory": bool(_ADVISORY.search(output)),
        "output": output[:500],
    }


def inspect_diff(repo: Path, files: list[str], issue_text: str, config: dict) -> dict:
    paths = [str(path) for path in files]
    decision = classify_paths(paths, config)
    if decision.get("lane") != "deps":
        return decision
    major = False
    extra: list[str] = []
    for path in paths:
        new = (repo / path).read_text(encoding="utf-8") if (repo / path).is_file() else ""
        old = _previous(repo, path)
        major = major or is_major(old, new)
        extra.extend(beyond_issue(old, new, issue_text))
    decision["major"] = major
    decision["extra"] = sorted(set(extra))
    decision["tier"] = "L" if major else ""
    return decision


def enter(run_dir: Path, config: dict) -> dict:
    issue_path = run_dir / "issue.json"
    if not issue_path.is_file() or not enabled(config):
        return {}
    issue = json.loads(issue_path.read_text(encoding="utf-8"))
    decision = classify_paths(planned_paths(issue), config)
    if not decision.get("lane") and not decision.get("split"):
        return {}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "deps.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return decision


def finalize(
    run_dir: Path,
    repo: Path,
    files: list[str],
    issue_text: str,
    config: dict,
    changelog: Path | None = None,
) -> dict:
    decision = inspect_diff(repo, files, issue_text, config)
    if decision.get("lane") != "deps":
        return decision
    notes = read_changelog(changelog)
    decision["changelog"] = bool(notes)
    decision["changelog_excerpt"] = notes[:240]
    found = audit(repo, config)
    decision["advisory"] = found["advisory"]
    decision["audit"] = found["source"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "deps.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return decision


def summary_lines(run_dir: Path) -> list[str]:
    data = _read(run_dir / "deps.json")
    if data.get("lane") != "deps":
        return []
    if data.get("advisory"):
        return ["", "WARNING: a dependency advisory blocks this run."]
    if data.get("major"):
        return ["", "Major dependency bump. Review tier is at least L and includes Codex."]
    return ["", f"Deps lane: {data.get('ecosystem')} only. The fast lane is off."]


def _previous(repo: Path, path: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"HEAD^:{path}"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("deps") or {}


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
