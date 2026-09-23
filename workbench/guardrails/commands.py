from workbench.guardrails.paths import Decision, GuardrailError

SHELL_METACHARACTERS = ";|&$`><\n"


class CommandDenied(GuardrailError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CommandPolicy:
    def __init__(self, allowlist: list[list[str]]) -> None:
        # an empty prefix would allow every command
        if any(not prefix for prefix in allowlist):
            raise ValueError("empty allowlist entry")
        self.allowlist = [list(prefix) for prefix in allowlist]

    def check(self, argv: list[str]) -> Decision:
        if not argv:
            return Decision(False, "empty command")
        if not shell_free(argv):
            return Decision(False, "shell metacharacters")
        if not any(_is_prefix(prefix, argv) for prefix in self.allowlist):
            return Decision(False, "command not allowlisted")
        return Decision(True)


def shell_free(argv: list[str]) -> bool:
    return not any(_has_metacharacter(arg) for arg in argv)


def _has_metacharacter(arg: str) -> bool:
    return any(char in arg for char in SHELL_METACHARACTERS)


def _is_prefix(prefix: list[str], argv: list[str]) -> bool:
    return len(prefix) <= len(argv) and argv[: len(prefix)] == prefix
