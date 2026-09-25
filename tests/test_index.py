import os
import re

import pytest

from workbench.db import connect
from workbench.retrieval import Chunk, RepoIndex

CITATION = re.compile(r"^[^:]+:\d+-\d+$")


@pytest.fixture
def conn():
    handle = connect(":memory:")
    yield handle
    handle.close()


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_python_defs_become_cited_chunks(tmp_path, conn):
    source = "\n".join(
        [
            "import os",
            "",
            "",
            "def alpha():",
            "    return os.sep",
            "",
            "",
            "",
            "",
            "",
            "def beta():",
            "    return 2",
            "",
        ]
    )
    write(tmp_path, "mod.py", source)
    index = RepoIndex(conn, tmp_path)
    index.index()

    chunks = index.chunks_for("mod.py")
    lines = source.splitlines()
    assert len(chunks) >= 2
    assert any(chunk.text.startswith("def beta") for chunk in chunks)
    for chunk in chunks:
        assert chunk.citation == f"mod.py:{chunk.start_line}-{chunk.end_line}"
        assert CITATION.match(chunk.citation)
        assert chunk.text == "\n".join(lines[chunk.start_line - 1 : chunk.end_line])


def test_markdown_is_chunked_into_blocks(tmp_path, conn):
    write(tmp_path, "README.md", "\n".join(f"line {n}" for n in range(1, 131)) + "\n")
    index = RepoIndex(conn, tmp_path)
    index.index()

    chunks = index.chunks_for("README.md")
    assert len(chunks) > 1
    assert chunks[-1].text.strip() != ""


def test_reindex_without_changes_skips_everything(tmp_path, conn):
    write(tmp_path, "a.py", "def one():\n    return 1\n")
    write(tmp_path, "b.md", "# title\n")
    index = RepoIndex(conn, tmp_path)
    first = index.index()
    before = index.stats()

    second = index.index()

    assert first["indexed"] == 2
    assert second["indexed"] == 0
    assert second["skipped"] == 2
    assert second["removed"] == 0
    assert second["chunks"] == before["chunks"]
    assert index.stats() == before


def test_changed_file_is_the_only_one_reindexed(tmp_path, conn):
    a = write(tmp_path, "a.py", "def one():\n    return 'OLD'\n")
    write(tmp_path, "b.py", "print('stable')\n")
    index = RepoIndex(conn, tmp_path)
    index.index()
    stable_before = index.chunks_for("b.py")

    a.write_text("def one():\n    return 'NEW'\n")
    result = index.index()

    assert result["indexed"] == 1
    assert result["skipped"] == 1
    assert all("OLD" not in chunk.text for chunk in index.chunks_for("a.py"))
    assert any("NEW" in chunk.text for chunk in index.chunks_for("a.py"))
    assert index.chunks_for("b.py") == stable_before


def test_deleted_file_loses_its_chunks(tmp_path, conn):
    write(tmp_path, "a.py", "def one():\n    return 1\n")
    b = write(tmp_path, "b.py", "def two():\n    return 2\n")
    index = RepoIndex(conn, tmp_path)
    index.index()
    assert index.chunks_for("b.py")

    os.remove(b)
    result = index.index()

    assert result["removed"] == 1
    assert index.chunks_for("b.py") == []
    assert index.stats()["files"] == 1


def test_binary_and_wrong_extension_are_ignored(tmp_path, conn):
    write(tmp_path, "ok.txt", "hello\n")
    (tmp_path / "blob.py").write_bytes(b"x = 1\x00\n")
    write(tmp_path, "notes.rst", "not an indexed extension\n")
    index = RepoIndex(conn, tmp_path)

    result = index.index()

    assert result["indexed"] == 1
    assert index.chunks_for("blob.py") == []
    assert index.chunks_for("notes.rst") == []
    assert index.stats()["files"] == 1


def test_vendor_dirs_are_skipped(tmp_path, conn):
    write(tmp_path, "keep.py", "def keep():\n    return 1\n")
    write(tmp_path, "node_modules/dep.py", "def dep():\n    return 2\n")
    write(tmp_path, ".venv/lib/site.py", "def site():\n    return 3\n")
    index = RepoIndex(conn, tmp_path)

    result = index.index()

    assert result["indexed"] == 1
    assert index.chunks_for("node_modules/dep.py") == []
    assert index.chunks_for(".venv/lib/site.py") == []


def test_chunks_for_orders_by_start_line(tmp_path, conn):
    source = "\n".join(f"def f{n}():\n    return {n}\n" for n in range(12))
    write(tmp_path, "many.py", source)
    index = RepoIndex(conn, tmp_path, max_chunk_lines=10, overlap_lines=2)
    index.index()

    chunks = index.chunks_for("many.py")
    starts = [chunk.start_line for chunk in chunks]
    assert len(chunks) > 1
    assert starts == sorted(starts)
    assert all(isinstance(chunk, Chunk) for chunk in chunks)


def test_long_python_chunks_overlap_without_gaps(tmp_path, conn):
    lines = ["def long():"] + [f"    value_{n} = {n}" for n in range(35)]
    write(tmp_path, "long.py", "\n".join(lines) + "\n")
    index = RepoIndex(conn, tmp_path, max_chunk_lines=10, overlap_lines=2)
    index.index()

    chunks = index.chunks_for("long.py")
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == len(lines)
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur.start_line <= prev.end_line
        assert cur.end_line > prev.end_line
    for chunk in chunks:
        assert chunk.text == "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
