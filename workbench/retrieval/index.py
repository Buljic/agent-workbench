import hashlib
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

EXTENSIONS = {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".txt", ".cfg", ".ini", ".sql", ".sh"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
BINARY_PROBE = 8192
DEF_TOKENS = ("def ", "class ", "async def ")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY,
        path TEXT,
        start_line INT,
        end_line INT,
        text TEXT,
        file_hash TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS chunks_path ON chunks (path)",
    """CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        file_hash TEXT,
        mtime REAL,
        size INT
    )""",
]


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str

    @property
    def citation(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


class RepoIndex:
    def __init__(
        self,
        conn: sqlite3.Connection,
        root: str | Path,
        max_chunk_lines: int = 60,
        overlap_lines: int = 5,
    ) -> None:
        self.conn = conn
        self.root = Path(root).resolve()
        self.max_chunk_lines = max_chunk_lines
        self.overlap_lines = overlap_lines
        with conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def index(self) -> dict:
        counts = {"indexed": 0, "skipped": 0, "removed": 0}
        seen: set[str] = set()
        with self.conn:
            for path in self._candidates():
                rel = path.relative_to(self.root).as_posix()
                try:
                    data = path.read_bytes()
                    info = path.stat()
                except OSError:
                    continue
                if b"\x00" in data[:BINARY_PROBE]:
                    continue

                seen.add(rel)
                digest = hashlib.sha256(data).hexdigest()
                row = self.conn.execute("SELECT file_hash FROM files WHERE path = ?", (rel,)).fetchone()
                if row and row[0] == digest:
                    counts["skipped"] += 1
                    continue

                self._store(rel, digest, info, data)
                counts["indexed"] += 1

            # files that vanished or turned binary since the last run
            stale = [row[0] for row in self.conn.execute("SELECT path FROM files") if row[0] not in seen]
            for rel in stale:
                self._drop(rel)
            counts["removed"] = len(stale)

        counts["chunks"] = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return counts

    def stats(self) -> dict:
        files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"files": files, "chunks": chunks}

    def chunks_for(self, path: str) -> list[Chunk]:
        rows = self.conn.execute(
            "SELECT path, start_line, end_line, text FROM chunks WHERE path = ? ORDER BY start_line, id",
            (path,),
        ).fetchall()
        return [Chunk(*row) for row in rows]

    def _candidates(self) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                # a symlink may point outside root, so confine the real path
                if not path.resolve().is_relative_to(self.root):
                    continue
                yield path

    def _store(self, rel: str, digest: str, info: os.stat_result, data: bytes) -> None:
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        text = data.decode("utf-8", errors="replace")
        ranges = split_chunks(text, self.max_chunk_lines, self.overlap_lines, is_python=rel.endswith(".py"))
        self.conn.executemany(
            "INSERT INTO chunks (path, start_line, end_line, text, file_hash) VALUES (?, ?, ?, ?, ?)",
            [(rel, start, end, body, digest) for start, end, body in ranges],
        )
        self.conn.execute(
            """INSERT INTO files (path, file_hash, mtime, size) VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                file_hash = excluded.file_hash,
                mtime = excluded.mtime,
                size = excluded.size""",
            (rel, digest, info.st_mtime, info.st_size),
        )

    def _drop(self, rel: str) -> None:
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        self.conn.execute("DELETE FROM files WHERE path = ?", (rel,))


def split_chunks(text: str, max_chunk_lines: int, overlap_lines: int, is_python: bool) -> list[tuple[int, int, str]]:
    """Return (start_line, end_line, text) with 1-based inclusive line numbers."""
    if max_chunk_lines < 1 or not 0 <= overlap_lines < max_chunk_lines:
        raise ValueError("need max_chunk_lines >= 1 and 0 <= overlap_lines < max_chunk_lines")

    lines = text.splitlines()
    # trailing blank lines carry nothing worth citing
    while lines and not lines[-1].strip():
        lines.pop()

    # python: every top-level definition opens a new segment
    cuts = [0]
    if is_python:
        cuts += [i for i, line in enumerate(lines) if i and line.startswith(DEF_TOKENS)]
    cuts.append(len(lines))

    chunks = []
    for lo, hi in pairwise(cuts):
        for start, stop in _windows(lo, hi, max_chunk_lines, overlap_lines):
            body = "\n".join(lines[start:stop])
            if body.strip():
                chunks.append((start + 1, stop, body))
    return chunks


def _windows(lo: int, hi: int, size: int, overlap: int) -> Iterator[tuple[int, int]]:
    # half-open 0-based ranges covering [lo, hi); each window starts overlap lines before the previous cut
    while True:
        cut = min(lo + size, hi)
        yield lo, cut
        if cut == hi:
            return
        lo = cut - overlap
