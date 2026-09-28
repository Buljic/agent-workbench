import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    item_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
)
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def insert_result(conn: sqlite3.Connection, item_id: str, payload: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO results (item_id, payload) VALUES (?, ?)",
        (item_id, json.dumps(payload)),
    )
    conn.commit()


def load_results(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT item_id, payload FROM results ORDER BY item_id").fetchall()
    return {item_id: json.loads(payload) for item_id, payload in rows}
