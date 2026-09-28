# JobRunner

JobRunner is a tiny service that pulls work items from a queue, checks each item and writes the results to a local sqlite file. It exists as a small fixture for code review and test triage, not as a production system.

The worker runs in one process. It fetches a batch over HTTP with a small helper, keeps the results of the current run in memory and hands them to the storage helper. Settings live in `config/settings.toml`, and the queue api key is read from the environment.
