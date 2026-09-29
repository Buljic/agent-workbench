# agent-workbench

## What this is

People keep asking whether an agent can read our repository, review a change, triage a failing test
or answer a question about the docs. Demos always say yes. The questions that matter come after:
what the agent may touch, what happens when it wants to write, and how we would find out what it
did. This repo is my attempt to build those parts first, as a small internal platform for code
review, test triage and documentation questions.

## Why an internal platform and not a chatbot

A chatbot can be asked to read `.env` or run a shell command, and nothing stops it or records what
it did. Before an agent gets near a real repository, a team needs allowlists, approvals for anything
that writes, a record of every step and a way to measure the answers. Those are the boring parts,
and the usual demo skips them. I wanted them tested before any model is plugged in, so that swapping
the model later leaves the rules alone.

## How it fits together

A run goes through a FastAPI endpoint into a planner and executor loop, which asks the model for one
JSON action per step: a tool call or a final answer. Every tool call passes a guardrail facade first. Paths must match an allowlist and miss a
deny list, and commands must match a prefix allowlist with no shell metacharacters. Every denial is
audited beside the allowed calls, and step and token budgets come from one shared object.
`open_issue` is a dry run behind an approval: the run waits in `WAITING_APPROVAL`, and a decision
runs the tool once and resumes from the saved history. Tool results carry file and line citations.
The retrieval package has an incremental indexer and a lexical BM25 search, exposed to the agent as
a `search_chunks` tool that returns the best chunks with path and line citations. Ten golden tasks run
over a committed synthetic repository in `evals/fixture_repo`, each on its own copy.

## Does it work?

Within what I can check here, yes. `make test` passes 139 tests covering the guardrails, the
budgets, the executor, the approval flow, the API and the evals. `tools/mutation_check.py` removes
the path guard, then the command guard, from a scratch copy, and both removals must make the
guardrail tests fail. `make demo` runs one review and prints its audit trail. `make eval` scores the
ten golden tasks. With the stub provider:

| task | type | status | passed | steps | tokens | seconds |
|---|---|---|---|---|---|---|
| review-race-results | review | COMPLETED | yes | 2 | 849 | 0.00 |
| review-fetch-timeout | review | COMPLETED | yes | 2 | 855 | 0.00 |
| review-page-offset | review | COMPLETED | yes | 2 | 838 | 0.00 |
| triage-find-encoding-test | test_triage | COMPLETED | yes | 2 | 547 | 0.00 |
| triage-run-single-file | test_triage | COMPLETED | yes | 2 | 560 | 0.35 |
| triage-propose-fix | test_triage | COMPLETED | yes | 3 | 1712 | 0.00 |
| docs-retry-policy | docs | COMPLETED | yes | 2 | 753 | 0.00 |
| docs-api-key-source | docs | COMPLETED | yes | 2 | 592 | 0.00 |
| docs-after-validation | docs | COMPLETED | yes | 2 | 739 | 0.00 |
| docs-sqlite-writer | docs | COMPLETED | yes | 2 | 564 | 0.00 |

The stub replays each golden script, so 10 of 10 holds by construction. It shows that the wiring
works end to end and says nothing about model quality. The demo answer is scripted for the same
reason.

TODO: a real model run is separate and not done yet. A 7B-class model on the GPU is next, once the
card is free. The principle is the same: same ten tasks, same keyword check, only the provider
changes (`--provider llama_cpp --base-url ... --model ...`).

## How to run

Create the environment with Python 3.11, then run the checks:

```
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
make test
make demo
make eval
PYTHONPATH=. .venv/bin/python tools/mutation_check.py
make serve
```

With `make serve` on port 8000, create a task and then a run. `GET /runs/<run id>/audit` returns the
trail for a run.

```
curl -s -X POST localhost:8000/tasks -H 'Content-Type: application/json' \
  -d '{"title": "review worker", "description": "Review app/server.py for races", "repo_path": "/abs/path/agent-workbench/evals/fixture_repo", "task_type": "review"}'
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' \
  -d '{"task_id": "<task id>", "provider": "stub", "script": ["{\"tool\": \"read_file\", \"arguments\": {\"path\": \"app/server.py\"}}", "{\"final\": \"app/server.py:5 RESULTS is shared\"}"]}'
```

## What is not done yet

There is no web UI, so everything goes through the API or the make targets. The approval flow needs
the caller to send a continuation script when the provider is the stub, because the stub keeps no
state between calls. Retrieval is lexical only: no embeddings, so paraphrased questions rank worse
than exact terms. The audit table and the jsonl log I
first sketched collapsed into one sqlite table. The API runs each run synchronously inside the
request, so a long run holds the request open until it finishes.
