import os
from pathlib import Path

# one place for the command allowlist so the api, the cli and the evals agree
ALLOWED_COMMANDS = [["pytest"], ["python", "-m", "pytest"]]

# tasks may only point at repositories under one of these roots; default is the cwd
REPO_ROOT_ENV = "WORKBENCH_REPO_ROOT"


def repo_roots() -> list[Path]:
    configured = os.environ.get(REPO_ROOT_ENV)
    if not configured:
        return [Path.cwd().resolve()]
    return [Path(part).resolve() for part in configured.split(os.pathsep) if part]


def check_repo_root(path: str, roots: list[str] | None = None) -> Path:
    """Reject repository paths outside the configured roots.

    The path guard is only as good as its root, so the root itself is
    constrained here before any policy is built on it.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("repo_path must be a non-empty string")
    candidate = Path(path).resolve()
    if not candidate.is_dir():
        raise ValueError("repo_path must be an existing directory")
    allowed = [Path(item).resolve() for item in roots] if roots else repo_roots()
    for root in allowed:
        if candidate.is_relative_to(root):
            return candidate
    raise ValueError(f"repo_path must be under {', '.join(str(r) for r in allowed)}")
