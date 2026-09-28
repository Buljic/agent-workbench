import json
from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenTask:
    name: str
    task_type: str
    description: str
    # lowercase; every keyword must appear in the final answer
    expected: list[str]
    # provider replies in order: tool calls, then a final answer
    stub_script: list[str]


def _call(tool: str, **arguments) -> str:
    return json.dumps({"tool": tool, "arguments": arguments})


def _final(answer: str) -> str:
    return json.dumps({"final": answer})


GOLDEN_TASKS = [
    GoldenTask(
        name="review-race-results",
        task_type="review",
        description="Review app/server.py for concurrency problems in the worker. Name the shared state and the missing protection.",
        expected=["results", "lock", "app/server.py"],
        stub_script=[
            _call("read_file", path="app/server.py"),
            _final(
                "app/server.py:5 RESULTS is a module level dict. worker writes to it at line 37 "
                "without a lock, so two workers running at once can race on the same keys. "
                "Wrap the writes in a threading.Lock."
            ),
        ],
    ),
    GoldenTask(
        name="review-fetch-timeout",
        task_type="review",
        description="Review fetch_json in app/server.py for network safety. Say what happens when the queue endpoint stops responding.",
        expected=["fetch_json", "timeout", "indefinitely"],
        stub_script=[
            _call("read_file", path="app/server.py"),
            _final(
                "app/server.py:9 fetch_json passes timeout straight to httpx.get, and its default "
                "is None. Without a timeout, a hung queue endpoint can block the worker indefinitely. "
                "Use a finite default such as 10 seconds."
            ),
        ],
    ),
    GoldenTask(
        name="review-page-offset",
        task_type="review",
        description="Review page() in app/server.py for correctness. Check the slice against the requested page size.",
        expected=["off-by-one", "page_size - 1", "app/server.py"],
        stub_script=[
            _call("read_file", path="app/server.py"),
            _final(
                "app/server.py:22 page() is off-by-one. The slice end is start + page_size - 1, "
                "so each page returns one item less than page_size. The slice should end at "
                "start + page_size."
            ),
        ],
    ),
    GoldenTask(
        name="triage-find-encoding-test",
        task_type="test_triage",
        description="Find the test that encodes the pagination bug. Name the test and the file it lives in.",
        expected=["test_page_first_slice", "tests/test_server.py"],
        stub_script=[
            _call("search_code", query="page("),
            _final(
                "tests/test_server.py:24 test_page_first_slice asserts page(list(range(10)), 0, 3) "
                "== [0, 1]. That test encodes the off-by-one slice, so it passes on the buggy code."
            ),
        ],
    ),
    GoldenTask(
        name="triage-run-single-file",
        task_type="test_triage",
        description="Give the command that runs only tests/test_server.py from the repo root, run it and report the exit code.",
        expected=["python -m pytest", "tests/test_server.py"],
        stub_script=[
            _call("run_tests", argv=["python", "-m", "pytest", "tests/test_server.py", "-q"]),
            _final(
                "Run only that file with python -m pytest tests/test_server.py -q from the repo root. "
                "The run finished with exit code 0."
            ),
        ],
    ),
    GoldenTask(
        name="triage-propose-fix",
        task_type="test_triage",
        description="Propose the smallest fix for the pagination bug and the test change that goes with it.",
        expected=["page_size", "[0, 1, 2]", "test_page_first_slice"],
        stub_script=[
            _call("read_file", path="app/server.py"),
            _call("read_file", path="tests/test_server.py"),
            _final(
                "In app/server.py:22 change the slice end to start + page_size, so page() returns "
                "items[start : start + page_size]. Then update test_page_first_slice in "
                "tests/test_server.py:26 to expect [0, 1, 2] for page(list(range(10)), 0, 3). "
                "Keep the change to these two lines."
            ),
        ],
    ),
    GoldenTask(
        name="docs-retry-policy",
        task_type="docs",
        description="What retry policy does the service use, and which operations does it apply to?",
        expected=["max_attempts = 3", "base_delay_ms = 250", "queue pull"],
        stub_script=[
            _call("read_file", path="docs/architecture.md"),
            _final(
                "Retries use max_attempts = 3 and base_delay_ms = 250, both set in the [retry] table "
                "of config/settings.toml. The retry policy applies to queue pulls only, and storage "
                "writes are not retried."
            ),
        ],
    ),
    GoldenTask(
        name="docs-api-key-source",
        task_type="docs",
        description="Where does the service read its api key from? Name the variable.",
        expected=["jobrunner_api_key", "environment"],
        stub_script=[
            _call("read_file", path="config/settings.toml"),
            _final(
                "The api key is read from the environment. settings.toml only holds the reference "
                "env:JOBRUNNER_API_KEY, so the value comes from the JOBRUNNER_API_KEY variable, "
                "never from the file."
            ),
        ],
    ),
    GoldenTask(
        name="docs-after-validation",
        task_type="docs",
        description="What happens to a result after validation passes?",
        expected=["after validation", "sqlite", "insert_result"],
        stub_script=[
            _call("read_file", path="docs/architecture.md"),
            _final(
                "Results are written after validation. worker keeps the result in RESULTS, and the "
                "storage helper in app/storage.py writes it to sqlite with insert_result, which uses "
                "a parameterized query."
            ),
        ],
    ),
    GoldenTask(
        name="docs-sqlite-writer",
        task_type="docs",
        description="Which module writes results to sqlite, and through which function?",
        expected=["app/storage.py", "insert_result"],
        stub_script=[
            _call("search_code", query="sqlite3"),
            _final(
                "app/storage.py writes to sqlite. insert_result runs a parameterized INSERT into the "
                "results table, and load_results reads the rows back."
            ),
        ],
    ),
]
