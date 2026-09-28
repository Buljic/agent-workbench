# Architecture

## 1. Queue pull

The worker asks the queue for a batch with `pull_batch` in `app/server.py`. The request goes through `fetch_json`, which takes the queue url and the batch size from `config/settings.toml`. Queue pulls are the only operation that uses the retry policy.

## 2. Validation

Each item must be a dict with an `id` and a `payload`. `validate` checks this. Items that fail the check are skipped and never stored.

## 3. Storage

Results are written after validation. `worker` builds each result with `process` and keeps it in the in-memory `RESULTS` map for the current run. The storage helper in `app/storage.py` writes results to sqlite with `insert_result`, which uses a parameterized query.

## 4. Retry policy

Queue pulls retry on transport errors with the values in the `[retry]` table: max_attempts = 3 and base_delay_ms = 250. The retry policy applies to queue pulls only. Storage writes are not retried.
