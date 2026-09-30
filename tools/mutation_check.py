"""Remove one guard at a time in a scratch copy and check that the guardrail tests fail."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKIP_NAMES = {".venv", "__pycache__", ".git", ".pytest_cache"}
SKIP_PATHS = {Path("evals/out")}
# (label, file to patch, the def line to patch under, the mutation to insert, test file to run)
MUTATIONS = [
    ("path guard", "workbench/guardrails/paths.py",
     "    def check(self, relative_path: str) -> Decision:",
     "        return Decision(True)\n", "tests/test_guardrails.py"),
    ("command guard", "workbench/guardrails/commands.py",
     "    def check(self, argv: list[str]) -> Decision:",
     "        return Decision(True)\n", "tests/test_guardrails.py"),
    ("secret redaction", "workbench/guardrails/secrets.py",
     "def redact(text: str) -> str:",
     "    return text\n", "tests/test_guardrails.py"),
]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="check that the guardrail tests catch a removed guard").parse_args(argv)
    source = Path.cwd()
    all_caught = True
    with tempfile.TemporaryDirectory() as tmp:
        # one copy per mutation, so a removed guard cannot hide the other result
        for index, (label, relative, signature, injection, test_file) in enumerate(MUTATIONS):
            copy = Path(tmp) / f"mutant-{index}"
            shutil.copytree(source, copy, ignore=_ignore(source))
            _remove_guard(copy / relative, signature, injection)
            all_caught = _run(copy, label, test_file) and all_caught
    return 0 if all_caught else 1


def _ignore(source: Path):
    def ignore(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).relative_to(source)
        return {name for name in names if name in SKIP_NAMES or name.endswith(".db") or relative / name in SKIP_PATHS}

    return ignore


def _remove_guard(path: Path, signature: str, injection: str) -> None:
    # an early return right under the def makes the guard a no-op
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == signature]
    if len(matches) != 1:
        raise RuntimeError(f"expected one '{signature.strip()}' in {path.name}, found {len(matches)}")
    lines.insert(matches[0] + 1, injection)
    path.write_text("".join(lines), encoding="utf-8")


def _run(copy: Path, label: str, test_file: str) -> bool:
    env = {**os.environ, "PYTHONPATH": str(copy)}
    command = [sys.executable, "-m", "pytest", test_file, "-q"]
    done = subprocess.run(command, cwd=copy, env=env, capture_output=True, text=True)
    failures = [line for line in done.stdout.splitlines() if line.startswith("FAILED ")]

    if done.returncode == 1 and failures:
        print(f"{label} removed -> tests failed as expected")
        for line in failures:
            print(f"  {line}")
        return True
    if done.returncode == 0:
        print(f"{label} removed -> TESTS STILL PASSED")
        return False
    # exit codes other than 0 and 1 mean pytest itself broke, not that a test failed
    print(f"{label} removed -> pytest exited {done.returncode}, not counted as caught")
    print(done.stdout[-500:] + done.stderr[-500:])
    return False


if __name__ == "__main__":
    raise SystemExit(main())
