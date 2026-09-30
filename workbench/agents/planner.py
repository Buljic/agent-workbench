import json
import re

from workbench.models import Task

SYSTEM_PROMPT = """You are a repository agent. Work on the task using the tools listed below.
Answer with exactly one JSON object per step, and nothing else.
To call a tool, answer {"tool": "<name>", "arguments": {...}}.
To finish, answer {"final": "<answer>"}.
Call at least one tool before you answer. Do not answer from memory.
Read the files your answer depends on, and cite them as path:line.
Once the tool output gives you enough evidence, answer with {"final": ...}.
Do not call open_issue unless the task asks to file an issue.
Never invent file contents, command output or test results.
Use only the tools listed here."""

FENCE = re.compile(r"```[A-Za-z0-9_-]*")


class PlanError(Exception):
    pass


def build_messages(task: Task, history: list[dict], tool_help: list[dict]) -> list[dict]:
    tools = "\n".join(f"- {tool['name']}: {tool['description']}" for tool in tool_help)
    system = f"{SYSTEM_PROMPT}\n\nTools:\n{tools or '- none'}"
    user = f"Task: {task.title}\n{task.description}\nRepo: {task.repo_path}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        *history,
    ]


def parse_action(text: str) -> dict:
    if not isinstance(text, str):
        raise PlanError("model output is not text")
    objects = _json_objects(FENCE.sub("", text))
    if not objects:
        raise PlanError("no JSON object found in model output")
    action = objects[-1]

    has_tool = "tool" in action
    has_final = "final" in action
    if has_tool == has_final:
        raise PlanError('action needs exactly one of "tool" or "final"')
    if has_final:
        if not isinstance(action["final"], str):
            raise PlanError('"final" must be a string')
        return action

    if not isinstance(action["tool"], str) or not action["tool"]:
        raise PlanError('"tool" must be a non-empty string')
    if not isinstance(action.get("arguments"), dict):
        raise PlanError('"arguments" must be an object')
    return action


def _json_objects(text: str) -> list[dict]:
    # only top-level objects; a nested object is part of its parent
    decoder = json.JSONDecoder()
    objects = []
    pos = 0
    while True:
        start = text.find("{", pos)
        if start == -1:
            return objects
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        objects.append(obj)
        pos = end
