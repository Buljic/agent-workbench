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
JSON action per step: a tool call or a final answer. Every tool call passes a guardrail facade first. Repository roots are confined to
`WORKBENCH_REPO_ROOT` (the working directory by default), paths must match an allowlist and miss a
deny list, commands must match a prefix allowlist with no shell metacharacters, and command
arguments may not point outside the repository. Secret patterns are redacted on the way into the
audit trail and on the way out of the search tools. Every denial is
audited beside the allowed calls, and step and token budgets come from one shared object.
`open_issue` is a dry run behind an approval: the run waits in `WAITING_APPROVAL`, and a decision
runs the tool once and resumes from the saved history. Tool results carry file and line citations.
The retrieval package has an incremental indexer and a lexical BM25 search, exposed to the agent as
a `search_chunks` tool that returns the best chunks with path and line citations. Ten golden tasks run
over a committed synthetic repository in `evals/fixture_repo`, each on its own copy.

## Does it work?

Within what I can check here, yes on the plumbing and no on the small models. `make test` passes
149 tests covering the guardrails, the budgets, the executor, the approval flow, the API and the
evals. `tools/mutation_check.py` removes the path guard, the command guard and the secret redaction
from scratch copies, one at a time, and each removal must make the guardrail tests fail; it reports
failure if any mutation slips through. `make demo` runs one review and prints its audit trail.
`make eval` scores the ten golden tasks.

With the stub provider the score is 10 of 10 by construction, since it replays each golden script.
That checks the wiring end to end and says nothing about model quality. For model quality I ran the
same ten tasks against two real models on CPU on 2026-09-30, with a llama.cpp build that has no GPU
support, niced on eight cores while the GPU was busy with other work:

| model | provider | passed | steps per task | notes |
|---|---|---|---|---|
| stub (scripted) | stub | 10 of 10 | 2-3 | pipeline check, not a measurement |
| llama3.2:1b | llama_cpp | 0 of 10 | 1 everywhere | answered without calling a single tool |
| llama3.1:8b-instruct-q4_K_M | llama_cpp | 2 of 10 | 4-8 | called tools, six runs hit the step budget |

The 8B run, from `evals/results/2026-09-30-llama31-8b-cpu.json`:

| task | type | status | passed | steps | tokens | seconds |
|---|---|---|---|---|---|---|
| review-race-results | review | BLOCKED | no | 8 | 5135 | 152.67 |
| review-fetch-timeout | review | BLOCKED | no | 8 | 5350 | 60.06 |
| review-page-offset | review | COMPLETED | no | 5 | 3057 | 39.61 |
| triage-find-encoding-test | test_triage | BLOCKED | no | 8 | 3272 | 40.77 |
| triage-run-single-file | test_triage | BLOCKED | no | 8 | 3852 | 92.11 |
| triage-propose-fix | test_triage | BLOCKED | no | 8 | 3170 | 31.91 |
| docs-retry-policy | docs | COMPLETED | yes | 4 | 1573 | 33.00 |
| docs-api-key-source | docs | COMPLETED | no | 8 | 3721 | 50.97 |
| docs-after-validation | docs | BLOCKED | no | 8 | 3662 | 133.99 |
| docs-sqlite-writer | docs | COMPLETED | yes | 7 | 4311 | 45.95 |

Both runs are committed whole: each JSON row carries the provider, model, base URL, status, steps,
tokens, seconds, the full answer and any error, so the sentences below can be checked against it.
A rerun at temperature zero should land close but not bit-identical, since sampling and timing both
move a little.

What the numbers say. The 1B model never called a tool in any task; it answered from memory in one
step, which is why it scores zero, and three of its answers were not even parseable as an
action. yeah, the small one is lowkey not built for this. The
8B model does the loop: it reads files, runs the test command, and cites paths. Six of its ten runs
still hit the eight-step budget, so it never reached an answer on those. Of the four that finished,
it passed the two documentation tasks outright, declared the off-by-one pagination correct (a real
miss), and answered the api key task with just "JOBRUNNER_API_KEY", which the strict keyword rubric
rejects because it never says the key is read from the environment. The rubric is deliberately
literal: every expected keyword has to appear.

The comparison is the reason the harness exists: the stub proves the pipeline, and the two models
show that the same tasks, budgets and rubric can tell a usable model from an unusable one.

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

To score a real model, start any OpenAI-compatible server and point the harness at it; on CPU only
machines, llama.cpp with no GPU layers works:

```
llama-server --model <model.gguf> --port 8000 -ngl 0 -t 8 -np 2 -c 4096
PYTHONPATH=. .venv/bin/python -m evals.run_eval --provider llama_cpp --base-url http://127.0.0.1:8000 --model <model name> --out evals/out/results.json
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

There is no authentication: the API assumes a single trusted operator on a local machine, and
`decided_by` on an approval is free text, so the approval gate records intent rather than proving
identity. There is no web UI either, so everything goes through the API or the make targets. The approval flow needs
the caller to send a continuation script when the provider is the stub, because the stub keeps no
state between calls. Retrieval is lexical only: no embeddings, so paraphrased questions rank worse
than exact terms. The audit table and the jsonl log I
first sketched collapsed into one sqlite table. The API runs each run synchronously inside the
request, so a long run holds the request open until it finishes.
