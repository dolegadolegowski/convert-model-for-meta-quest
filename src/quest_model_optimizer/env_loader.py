from __future__ import annotations

import os
from pathlib import Path


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_local_env_file(project_root: Path, *, filename: str = ".cmq_worker.env") -> Path | None:
    """Load local KEY=VALUE entries into process environment if file exists.

    Existing environment variables are preserved and never overwritten.
    Returns resolved env-file path when loaded, otherwise None.
    """

    env_path = (project_root / filename).resolve()
    if not env_path.exists() or not env_path.is_file():
        return None

    raw_text = env_path.read_text(encoding="utf-8")
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or not (key[0].isalpha() or key[0] == "_"):
            continue
        if key in os.environ:
            continue
        parsed_value = _strip_matching_quotes(value.strip())
        os.environ[key] = parsed_value
    return env_path
