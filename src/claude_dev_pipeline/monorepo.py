"""Scope a run to the workspaces a change actually touches."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_MANIFESTS = ("package.json", "go.mod", "Cargo.toml", "pyproject.toml")


def enabled(config: dict) -> bool:
    return _section(config).get("enabled", True) is not False


def workspaces(repo: Path, config: dict) -> list[str]:
    """Package directories from workspace manifests. Empty for a single package."""
    if not enabled(config) or not repo.is_dir():
        return []
    found: list[str] = []
    found.extend(_npm(repo))
    found.extend(_pnpm(repo))
    found.extend(_go(repo))
    found.extend(_cargo(repo))
    found.extend(_python(repo))
    found.extend(_markers(repo))
    extra = _section(config).get("packages") or []
    for item in extra:
        found.append(str(item).strip("/"))
    unique: list[str] = []
    for item in found:
        if item and item not in unique:
            unique.append(item)
    return unique


def affected(files: list[str], packages: list[str], repo: Path | None = None) -> list[str]:
    hits: list[str] = []
    for path in files:
        match = ""
        for package in packages:
            prefix = package.rstrip("/") + "/"
            if path == package or path.startswith(prefix):
                if len(package) > len(match):
                    match = package
        if not match and repo is not None:
            match = nearest(repo, path)
        if match and match not in hits:
            hits.append(match)
    return hits


def nearest(repo: Path, path: str) -> str:
    """Fallback: the directory of the nearest manifest above the file."""
    current = repo / Path(path).parent
    while True:
        if any((current / name).is_file() for name in _MANIFESTS):
            relative = current.relative_to(repo).as_posix()
            return "" if relative == "." else relative
        if current == repo:
            return ""
        current = current.parent


def select_files(files: list[str], config: dict, sizes: dict[str, int] | None = None) -> list[str]:
    """Keep the first files that fit the file and token caps. Do not read the rest."""
    section = _section(config)
    max_files = int(section.get("max_files") or 40)
    max_tokens = int(section.get("max_tokens") or 8000)
    chosen: list[str] = []
    tokens = 0
    for path in files:
        if len(chosen) >= max_files:
            break
        size = int((sizes or {}).get(path, 200))
        cost = max(1, size // 4)
        if chosen and tokens + cost > max_tokens:
            break
        chosen.append(path)
        tokens += cost
    return chosen


def suite_scope(tier: str, config: dict) -> str:
    full = [str(item) for item in (_section(config).get("full_suite_tiers") or ["L", "XL"])]
    return "full" if tier in full else "affected"


def apply_sparse(repo: Path, paths: list[str]) -> dict:
    """Optional sparse checkout. The caller enables it; the default is off."""
    if not paths:
        return {"applied": False, "reason": "no paths"}
    init = _git(repo, "sparse-checkout", "init", "--cone")
    if init.returncode != 0:
        return {"applied": False, "reason": (init.stderr or "sparse-checkout unavailable")[:200]}
    listed = _git(repo, "sparse-checkout", "set", *paths)
    return {"applied": listed.returncode == 0, "paths": list(paths)}


def note(run_dir: Path, repo: Path, files: list[str], tier: str, config: dict) -> dict:
    if not enabled(config):
        return {"packages": []}
    packages = workspaces(repo, config)
    if not packages and not files:
        return {"packages": []}
    scope = affected(files, packages, repo)
    chosen = select_files(files, config)
    report = {
        "packages": scope or packages,
        "files": chosen,
        "test_scope": suite_scope(tier, config) if packages else "full",
        "sparse": False,
    }
    if _section(config).get("sparse") is True and scope:
        result = apply_sparse(repo, scope)
        report["sparse"] = bool(result.get("applied"))
    if not report["packages"] and not scope:
        return report
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "monorepo.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def affected_suffix(run_dir: Path | None) -> str:
    if run_dir is None:
        return ""
    path = run_dir / "monorepo.json"
    if not path.is_file():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("test_scope") != "affected":
        return ""
    return " ".join(str(item) for item in data.get("packages") or [])


def summary_lines(run_dir: Path) -> list[str]:
    path = run_dir / "monorepo.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    packages = ", ".join(data.get("packages") or []) or "none"
    return ["", f"Monorepo scope: {packages}. Tests: {data.get('test_scope')}."]


def _npm(repo: Path) -> list[str]:
    path = repo / "package.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    raw = data.get("workspaces") or []
    if isinstance(raw, dict):
        raw = raw.get("packages") or []
    return _expand(repo, [str(item) for item in raw])


def _pnpm(repo: Path) -> list[str]:
    path = repo / "pnpm-workspace.yaml"
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            patterns.append(stripped[2:].strip("\"'"))
    return _expand(repo, patterns)


def _go(repo: Path) -> list[str]:
    path = repo / "go.work"
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("use "):
            found.append(stripped.split(None, 1)[1].strip("./"))
    return [item for item in found if (repo / item).is_dir()]


def _cargo(repo: Path) -> list[str]:
    path = repo / "Cargo.toml"
    if not path.is_file():
        return []
    members = []
    capture = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("[workspace]"):
            capture = True
            continue
        if capture and line.startswith("["):
            break
        if capture and "members" in line:
            members.extend(_quoted(line))
    return [item for item in members if (repo / item).is_dir()]


def _python(repo: Path) -> list[str]:
    path = repo / "pyproject.toml"
    if not path.is_file():
        return []
    members = []
    capture = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if "workspace" in line and line.strip().startswith("["):
            capture = True
            continue
        if capture and line.startswith("["):
            break
        if capture and "members" in line:
            members.extend(_quoted(line))
    return [item for item in members if (repo / item).is_dir()]


def _markers(repo: Path) -> list[str]:
    found = []
    for name in ("nx.json", "turbo.json", "WORKSPACE", "pants.toml"):
        if (repo / name).is_file():
            for folder in ("packages", "apps", "libs"):
                root = repo / folder
                if root.is_dir():
                    found.extend(
                        path.relative_to(repo).as_posix()
                        for path in sorted(root.iterdir())
                        if path.is_dir()
                    )
    return found


def _expand(repo: Path, patterns: list[str]) -> list[str]:
    found = []
    for pattern in patterns:
        if any(token in pattern for token in "*?["):
            for path in sorted(repo.glob(pattern)):
                if path.is_dir():
                    found.append(path.relative_to(repo).as_posix())
        elif (repo / pattern).is_dir():
            found.append(pattern.strip("/"))
    return found


def _quoted(line: str) -> list[str]:
    body = line.split("[", 1)[-1].split("]")[0]
    found = []
    for item in body.split(","):
        cleaned = item.strip().strip("\"'")
        if cleaned:
            found.append(cleaned)
    return found


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("monorepo") or {}
