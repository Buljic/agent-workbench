import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TASK = {
    "title": "review app",
    "description": "check the app for sockets",
    "task_type": "review",
}


def step(**action):
    return json.dumps(action)


def read(path):
    return step(tool="read_file", arguments={"path": path})


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("import socket\n")
    return root


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "t.db")))


@pytest.fixture
def task_id(client, repo):
    response = client.post("/tasks", json={**TASK, "repo_path": str(repo)})
    return response.json()["id"]


def post_run(client, task_id, script, **extra):
    return client.post("/runs", json={"task_id": task_id, "provider": "stub", "script": script, **extra})


def events(client, run_id):
    response = client.get(f"/runs/{run_id}/audit")
    return [record["event"] for record in response.json()["records"]]


def test_create_task_returns_201_and_32_char_id(client, repo):
    response = client.post("/tasks", json={**TASK, "repo_path": str(repo)})

    assert response.status_code == 201
    assert len(response.json()["id"]) == 32


def test_get_task_returns_task_and_unknown_id_is_404(client, repo, task_id):
    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["title"] == TASK["title"]
    assert response.json()["repo_path"] == str(repo)

    missing = client.get("/tasks/does-not-exist")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "task not found"}


def test_stub_run_reads_file_then_completes(client, task_id):
    response = post_run(client, task_id, [read("src/app.py"), step(final="socket is imported")])

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["steps"] == 2
    assert body["result"] == "socket is imported"
    assert body["provider"] == "stub"


def test_audit_contains_run_started_and_run_finished(client, task_id):
    run_id = post_run(client, task_id, [read("src/app.py"), step(final="done")]).json()["id"]

    response = client.get(f"/runs/{run_id}/audit")

    assert response.status_code == 200
    names = events(client, run_id)
    assert "run_started" in names
    assert "run_finished" in names


def test_read_outside_root_is_audited_and_run_recovers(client, task_id):
    script = [read("../secret.txt"), step(final="recovered")]
    body = post_run(client, task_id, script).json()

    assert body["status"] == "COMPLETED"
    assert body["result"] == "recovered"

    records = client.get(f"/runs/{body['id']}/audit").json()["records"]
    blocked = [record for record in records if record["event"] == "tool_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["payload"]["tool"] == "read_file"
    assert blocked[0]["payload"]["reason"] == "parent segment in path"


def test_run_for_unknown_task_is_404(client):
    response = client.post("/runs", json={"task_id": "missing", "script": []})

    assert response.status_code == 404
    assert response.json() == {"detail": "task not found"}


def test_max_steps_one_with_endless_script_is_blocked(client, task_id):
    script = [read("src/app.py")] * 10
    body = post_run(client, task_id, script, max_steps=1).json()

    assert body["status"] == "BLOCKED"
    assert body["steps"] == 1
    assert "budget_exceeded" in events(client, body["id"])


def test_unknown_run_and_audit_are_404(client):
    assert client.get("/runs/missing").status_code == 404
    assert client.get("/runs/missing/audit").status_code == 404


def test_llama_cpp_without_base_url_is_422(client, task_id):
    response = client.post("/runs", json={"task_id": task_id, "provider": "llama_cpp"})

    assert response.status_code == 422


def test_tool_arguments_are_redacted_in_the_audit(client, repo):
    (repo / "src" / "app.py").write_text("import os\nAPI = 'sk-abcdefghijklmnop1234'\n")
    task = client.post("/tasks", json={**TASK, "repo_path": str(repo)}).json()
    script = [step(tool="read_file", arguments={"path": "src/app.py"}), step(final="done")]
    run = post_run(client, task["id"], script).json()
    blob = json.dumps(client.get(f"/runs/{run['id']}/audit").json()["records"])
    assert "sk-abcdefghijklmnop1234" not in blob
    assert task["id"] in blob


def test_open_issue_waits_for_approval(client, repo, task_id):
    script = [step(tool="open_issue", arguments={"title": "flaky test", "body": "retry in ci"})]
    run = post_run(client, task_id, script).json()
    assert run["status"] == "WAITING_APPROVAL"
    pending = client.get(f"/runs/{run['id']}/approvals").json()["pending"]
    assert pending is not None
    assert pending["tool"] == "open_issue"


def test_approved_tool_runs_and_the_loop_continues(client, repo, task_id):
    script = [step(tool="open_issue", arguments={"title": "t", "body": "b"})]
    run = post_run(client, task_id, script).json()
    pending = client.get(f"/runs/{run['id']}/approvals").json()["pending"]
    body = {"approved": True, "decided_by": "imad", "script": [step(final="issue created")]}
    decided = client.post(f"/runs/{run['id']}/approvals/{pending['id']}", json=body)
    assert decided.status_code == 200
    payload = decided.json()
    assert payload["status"] == "COMPLETED"
    assert payload["result"] == "issue created"
    audit = [record["event"] for record in client.get(f"/runs/{run['id']}/audit").json()["records"]]
    assert "approval_decided" in audit


def test_rejected_approval_fails_the_run(client, repo, task_id):
    script = [step(tool="open_issue", arguments={"title": "t", "body": "b"})]
    run = post_run(client, task_id, script).json()
    pending = client.get(f"/runs/{run['id']}/approvals").json()["pending"]
    body = {"approved": False, "decided_by": "imad"}
    decided = client.post(f"/runs/{run['id']}/approvals/{pending['id']}", json=body).json()
    assert decided["status"] == "FAILED"
    assert "rejected by imad" in decided["error"]
    assert client.get(f"/runs/{run['id']}/approvals").json()["pending"] is None
