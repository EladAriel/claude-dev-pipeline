"""Docs lane for changes that only touch docs or non-executable config."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[\w.-]+)")
_CODE = {
    ".py",
    ".go",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".rb",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
}
_DOCS = ("docs/**", "*.md", "**/*.md", "**/*.rst", "**/*.adoc", "LICENSE", "CHANGELOG.md")
_CONFIG = (".editorconfig", ".markdownlint*", "mkdocs.yml", "vale.ini", "**/.prettierrc")
_RUNTIME = (
    ".github/workflows/**",
    "**/.github/workflows/**",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "requirements.txt",
    "requirements-*.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "Gemfile",
    "Gemfile.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "*.tf",
    "**/Chart.yaml",
)
_LIGHT = {"lint", "format", "link", "schema"}


def enabled(config: dict) -> bool:
    section = _section(config)
    return section.get("enabled", True) is not False


def planned_paths(issue: dict) -> list[str]:
    text = f"{issue.get('title') or ''}\n{issue.get('body') or ''}"
    found: list[str] = []
    for match in _PATH.findall(text):
        cleaned = match.rstrip(".,:;)")
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def kind_of(path: str, config: dict) -> str:
    """Return docs, runtime, or code. Runtime includes CI, manifests, and infra."""
    section = _section(config)
    runtime = list(section.get("runtime_globs") or _RUNTIME)
    docs = list(section.get("docs_globs") or _DOCS)
    config_globs = list(section.get("config_globs") or _CONFIG)
    if _match(path, runtime) or Path(path).suffix.lower() in _CODE:
        return "runtime" if _match(path, runtime) else "code"
    if _match(path, docs) or _match(path, config_globs):
        return "docs"
    return "code"


def route(issue: dict, config: dict) -> dict | None:
    """Docs lane only when every planned path is docs or non-executable config."""
    if not enabled(config):
        return None
    paths = planned_paths(issue)
    if not paths:
        return None
    kinds = {kind_of(path, config) for path in paths}
    if kinds != {"docs"}:
        return None
    section = _section(config)
    return {
        "lane": "docs",
        "paths": paths,
        "budget_cents": int(section.get("budget_cents") or 200),
        "review": "bug-hunt",
        "escalated": False,
    }


def leaves_docs(files: list[str], config: dict) -> bool:
    return any(kind_of(path, config) != "docs" for path in files)


def active(run_dir: Path) -> bool:
    data = _read(run_dir / "docs.json")
    return data.get("lane") == "docs" and not data.get("escalated")


def enter(run_dir: Path, config: dict, work_cap: int) -> tuple[bool, int]:
    issue_path = run_dir / "issue.json"
    if not issue_path.is_file():
        return False, work_cap
    issue = json.loads(issue_path.read_text(encoding="utf-8"))
    decision = route(issue, config)
    if decision is None:
        return False, work_cap
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "docs.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True, min(work_cap, int(decision["budget_cents"]))


def mark_escalated(run_dir: Path) -> None:
    data = _read(run_dir / "docs.json")
    data["escalated"] = True
    data["lane"] = "standard"
    (run_dir / "docs.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def light_names(docs: bool) -> set[str] | None:
    return _LIGHT if docs else None


def summary_lines(run_dir: Path) -> list[str]:
    data = _read(run_dir / "docs.json")
    if data.get("lane") != "docs" and not data.get("escalated"):
        return []
    if data.get("escalated"):
        return ["", "Docs lane escalated because the diff touches code or runtime config."]
    return ["", "Docs lane: spec and plan were skipped. Review is bug-hunt only."]


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("docs_lane") or {}


def _match(path: str, patterns: list[str]) -> bool:
    name = Path(path).name
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if pattern.startswith("**/") and (
            fnmatch.fnmatch(name, pattern[3:]) or fnmatch.fnmatch(path, pattern[3:])
        ):
            return True
    return False


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
