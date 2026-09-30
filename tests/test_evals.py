import hashlib

from evals.run_eval import FIXTURE, render_table, run_suite
from evals.tasks import GOLDEN_TASKS
from workbench.agents.planner import parse_action

SERVER = FIXTURE / "app" / "server.py"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_golden_tasks_shape():
    assert len(GOLDEN_TASKS) == 10
    names = [task.name for task in GOLDEN_TASKS]
    assert len(set(names)) == len(names)
    for task in GOLDEN_TASKS:
        assert task.expected, task.name


def test_final_reply_contains_all_keywords():
    for task in GOLDEN_TASKS:
        action = parse_action(task.stub_script[-1])
        assert "final" in action, task.name
        answer = action["final"].lower()
        missing = [word for word in task.expected if word not in answer]
        assert missing == [], task.name


def test_every_script_uses_a_tool_before_final():
    for task in GOLDEN_TASKS:
        calls = task.stub_script[:-1]
        assert calls, task.name
        assert all("tool" in parse_action(reply) for reply in calls), task.name


def test_run_suite_stub_passes_all_tasks(tmp_path):
    rows = run_suite("stub", workdir=tmp_path)
    assert len(rows) == len(GOLDEN_TASKS)
    failed = [row["task"] for row in rows if not row["passed"]]
    assert failed == []


def test_fixture_copy_is_left_untouched(tmp_path):
    before = _sha256(SERVER)
    run_suite("stub", workdir=tmp_path)
    assert _sha256(SERVER) == before


def test_render_table_has_header_and_one_line_per_task():
    rows = [
        {
            "task": task.name,
            "type": task.task_type,
            "status": "COMPLETED",
            "passed": True,
            "steps": 2,
            "tokens": 100,
            "seconds": 0.5,
        }
        for task in GOLDEN_TASKS
    ]
    lines = render_table(rows).splitlines()
    assert lines[0] == "| task | type | status | passed | steps | tokens | seconds |"
    assert len(lines) == len(rows) + 2
    for task, line in zip(GOLDEN_TASKS, lines[2:]):
        assert line.startswith(f"| {task.name} |")
        assert line.endswith("| 0.50 |")


def test_scorer_fails_an_answer_without_the_keywords(tmp_path):
    from evals.tasks import GoldenTask

    weak = GoldenTask(
        name="weak-answer",
        task_type="docs",
        description="say nothing useful",
        expected=["a_keyword_the_model_never_says"],
        stub_script=['{"final": "I could not find that."}'],
    )
    rows = run_suite("stub", workdir=tmp_path, tasks=[weak])
    assert rows[0]["status"] == "COMPLETED"
    assert rows[0]["passed"] is False


def test_scorer_passes_an_answer_with_every_keyword(tmp_path):
    from evals.tasks import GoldenTask

    good = GoldenTask(
        name="good-answer",
        task_type="docs",
        description="say the words",
        expected=["retry", "attempts"],
        stub_script=['{"final": "the retry policy allows three attempts"}'],
    )
    rows = run_suite("stub", workdir=tmp_path, tasks=[good])
    assert rows[0]["passed"] is True
