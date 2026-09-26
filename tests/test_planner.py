import pytest

from workbench.agents.planner import SYSTEM_PROMPT, PlanError, build_messages, parse_action
from workbench.models import Task

TOOLS = [
    {"name": "read_file", "description": "Read a text file from the repository."},
    {"name": "search_code", "description": "Case-insensitive text search across the repository."},
]


def make_task():
    return Task(title="review the app", description="look for socket use", repo_path="/repo", task_type="review")


def test_parse_bare_json_object():
    assert parse_action('{"tool": "read_file", "arguments": {"path": "a.py"}}') == {
        "tool": "read_file",
        "arguments": {"path": "a.py"},
    }


def test_parse_fenced_code_block():
    text = '```json\n{"final": "done"}\n```'
    assert parse_action(text) == {"final": "done"}


def test_parse_prose_with_trailing_json():
    text = 'I will read the file now.\n{"tool": "read_file", "arguments": {"path": "a.py"}}'
    assert parse_action(text)["tool"] == "read_file"


def test_parse_takes_last_top_level_object():
    text = '{"final": "old"} then {"final": "new"}'
    assert parse_action(text) == {"final": "new"}


def test_parse_nested_arguments_stay_whole():
    text = '{"tool": "search_code", "arguments": {"query": "x", "path": "src"}}'
    assert parse_action(text)["arguments"] == {"query": "x", "path": "src"}


def test_invalid_json_raises():
    with pytest.raises(PlanError):
        parse_action("not json")


def test_object_with_both_keys_raises():
    with pytest.raises(PlanError, match="exactly one"):
        parse_action('{"tool": "read_file", "arguments": {}, "final": "x"}')


def test_tool_without_arguments_raises():
    with pytest.raises(PlanError, match="arguments"):
        parse_action('{"tool": "read_file"}')


def test_tool_with_non_string_name_raises():
    with pytest.raises(PlanError):
        parse_action('{"tool": 3, "arguments": {}}')


def test_build_messages_includes_prompt_task_and_tools():
    task = make_task()
    history = [{"role": "user", "content": "read_file -> x = 1"}]

    messages = build_messages(task, history, TOOLS)

    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert SYSTEM_PROMPT in messages[0]["content"]
    assert "read_file" in messages[0]["content"]
    assert "search_code" in messages[0]["content"]
    assert "review the app" in messages[1]["content"]
    assert messages[2] == history[0]
