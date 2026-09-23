import re

# order matters: the specific kinds run first so long_hex does not relabel them.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{12,}\b")),
    ("pem_block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    )),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*[=:]\s*\S+")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("long_hex", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
]


def redact(text: str) -> str:
    for kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(f"[redacted:{kind}]", text)
    return text


def has_secret(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in SECRET_PATTERNS)
