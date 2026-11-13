from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    here = Path(__file__).resolve()
    for path in (here, *here.parents):
        if (path / "pyproject.toml").exists():
            return path
    raise RuntimeError("repo root not found")
