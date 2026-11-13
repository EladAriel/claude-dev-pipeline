"""Run local `codex exec` read-only and parse its JSON. No network calls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class CodexError(Exception):
    pass


def run_codex(
    *,
    schema: Path,
    output: Path,
    prompt: str,
    timeout: float = 30,
) -> dict:
    """Return parsed Codex JSON. Tokens stay null when the payload omits them."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema),
        "-o",
        str(output),
        "-m",
        "gpt-6.1-sol",
    ]
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CodexError("codex executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodexError("codex timed out") from exc
    if completed.returncode != 0:
        raise CodexError(completed.stderr.strip() or "codex failed")
    raw = output.read_text(encoding="utf-8") if output.is_file() else completed.stdout
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexError("codex output is not JSON") from exc
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    data["tokens"] = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }
    return data
