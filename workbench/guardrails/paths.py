import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

DEFAULT_DENY_GLOBS = [".git/*", ".git/**", "*.env", ".env*", "*.pem", "*.key", "id_rsa*", "secrets*"]


class GuardrailError(Exception):
    pass


class PathDenied(GuardrailError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None


class PathPolicy:
    def __init__(self, root: str | Path, allow_globs: list[str], deny_globs: list[str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.allow_globs = list(allow_globs)
        self.deny_globs = list(DEFAULT_DENY_GLOBS if deny_globs is None else deny_globs)

    def resolve(self, relative_path: str) -> Path:
        if not relative_path:
            raise PathDenied("empty path")
        if "\x00" in relative_path:
            raise PathDenied("nul byte in path")
        if PurePosixPath(relative_path).is_absolute() or PureWindowsPath(relative_path).is_absolute():
            raise PathDenied("absolute path")
        # treat backslash as a separator too, so "..\\x" cannot slip through on posix
        if ".." in relative_path.replace("\\", "/").split("/"):
            raise PathDenied("parent segment in path")
        try:
            candidate = (self.root / relative_path).resolve()
        except (OSError, RuntimeError) as exc:
            raise PathDenied(f"cannot resolve path: {exc}") from exc
        # symlinks are already followed, so this catches links that point out of root
        if not candidate.is_relative_to(self.root):
            raise PathDenied("path resolves outside root")
        return candidate

    def check(self, relative_path: str) -> Decision:
        try:
            self.resolve(relative_path)
        except PathDenied as exc:
            return Decision(False, exc.reason)
        posix = PurePosixPath(relative_path).as_posix()
        basename = PurePosixPath(relative_path).name
        for pattern in self.deny_globs:
            if fnmatch.fnmatch(posix, pattern) or fnmatch.fnmatch(basename, pattern):
                return Decision(False, f"denied by {pattern}")
        if any(fnmatch.fnmatch(posix, pattern) for pattern in self.allow_globs):
            return Decision(True)
        return Decision(False, "not in allowlist")
