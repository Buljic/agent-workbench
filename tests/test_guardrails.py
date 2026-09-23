import hashlib
import os

import pytest

from workbench.guardrails import CommandPolicy, PathDenied, PathPolicy
from workbench.guardrails.commands import shell_free
from workbench.guardrails.secrets import has_secret, redact


@pytest.fixture
def paths(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    return PathPolicy(tmp_path, ["src/**", "tests/**"])


@pytest.fixture
def commands():
    return CommandPolicy([["pytest"], ["python", "-m", "pytest"]])


def test_allowed_read(paths):
    assert paths.check("src/app.py").allowed


def test_not_in_allowlist(paths):
    decision = paths.check("README.md")
    assert not decision.allowed
    assert decision.reason == "not in allowlist"


def test_parent_segment_denied(paths):
    assert not paths.check("../outside.py").allowed
    with pytest.raises(PathDenied):
        paths.resolve("../outside.py")


def test_absolute_path_denied(paths):
    assert not paths.check("/etc/passwd").allowed
    with pytest.raises(PathDenied):
        paths.resolve("/etc/passwd")


def test_git_dir_denied_even_with_wildcard_allow(tmp_path):
    policy = PathPolicy(tmp_path, ["**"])
    assert not policy.check(".git/config").allowed


def test_env_file_denied(tmp_path):
    policy = PathPolicy(tmp_path, ["**"])
    assert not policy.check(".env").allowed


def test_symlink_out_of_root_denied(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("not for the agent\n")
    (tmp_path / "src").mkdir()
    link = tmp_path / "src" / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("platform cannot create symlinks")
    decision = PathPolicy(tmp_path, ["src/**"]).check("src/link.txt")
    assert not decision.allowed
    assert decision.reason == "path resolves outside root"


def test_allowlisted_commands(commands):
    assert commands.check(["pytest", "-q"]).allowed
    assert commands.check(["python", "-m", "pytest", "tests"]).allowed


def test_command_not_allowlisted(commands):
    decision = commands.check(["rm", "-rf", "/"])
    assert not decision.allowed
    assert decision.reason == "command not allowlisted"


def test_command_with_metacharacter_denied(commands):
    decision = commands.check(["pytest", "-q;", "rm"])
    assert not decision.allowed
    assert decision.reason == "shell metacharacters"


def test_empty_command_denied(commands):
    decision = commands.check([])
    assert not decision.allowed
    assert decision.reason == "empty command"


def test_shell_interpreter_denied(commands):
    assert not commands.check(["bash", "-c", "ls"]).allowed


def test_shell_free_detects_newline():
    assert shell_free(["pytest", "-q"])
    assert not shell_free(["echo", "a\nb"])


def test_redacts_api_key():
    assert "[redacted:api_key]" in redact("key sk-abcdefghijklmnop1234")


def test_redacts_password_value():
    assert "hunter2" not in redact("password=hunter2")


def test_redacts_aws_key():
    assert "[redacted:aws_key]" in redact("AKIAABCDEFGHIJKLMNOP")


def test_plain_text_unchanged():
    text = "the build finished in 42 seconds"
    assert redact(text) == text


def test_pem_header_is_secret():
    assert has_secret("-----BEGIN RSA PRIVATE KEY-----")
    assert not has_secret("the quick brown fox jumps over the lazy dog")


def test_hex_digest_redacted_but_long_word_kept():
    digest = hashlib.sha1(b"workbench").hexdigest()
    word = "incomprehensibilitiesovergeneralizations"
    assert len(digest) == 40
    assert len(word) == 40
    assert "[redacted:long_hex]" in redact(digest)
    assert redact(word) == word


def test_deny_globs_match_basenames_in_subdirectories(tmp_path):
    policy = PathPolicy(tmp_path, ["**"])
    assert not policy.check("config/secrets.yaml").allowed
    assert not policy.check("keys/id_rsa").allowed
    assert not policy.check("nested/.env").allowed


def test_pem_block_body_is_redacted():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    out = redact(text)
    assert "MIIEowIBAAKCAQEA" not in out
    assert "[redacted:pem_block]" in out
