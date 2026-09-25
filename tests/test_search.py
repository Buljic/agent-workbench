import re

import pytest

from workbench.db import connect
from workbench.retrieval import RepoIndex
from workbench.retrieval.search import ChunkSearch, format_hits

HEAVY = "service/heavy.py"
PHRASE = "connection pool exhaustion"

HEAVY_SRC = """\
def drain(queue):
    # connection pool exhaustion usually means leaked connections
    # check the queue before connection pool exhaustion gets logged
    if not queue:
        raise RuntimeError("connection pool exhaustion")
    return queue.pop()
"""


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


@pytest.fixture
def indexed(tmp_path, conn):
    root = tmp_path / "repo"
    write(root, HEAVY, HEAVY_SRC)
    write(root, "service/light.py", "# the pool size comes from config\n\nLIMIT = 10\n")
    write(root, "service/unrelated.py", 'def render(name):\n    return f"hello {name}"\n')
    RepoIndex(conn, root).index()
    return ChunkSearch(conn), root


def test_heavy_file_ranks_first_with_cited_range(indexed):
    search, root = indexed
    hits = search.search(PHRASE)

    assert hits[0].path == HEAVY
    lines = (root / HEAVY).read_text().splitlines()
    assert 1 <= hits[0].start_line <= hits[0].end_line <= len(lines)
    assert hits[0].citation == f"{HEAVY}:{hits[0].start_line}-{hits[0].end_line}"

    mentions = [num for num, line in enumerate(lines, start=1) if PHRASE in line]
    assert len(mentions) >= 2
    assert hits[0].start_line <= mentions[0] and mentions[-1] <= hits[0].end_line


def test_query_in_no_chunk_returns_empty(indexed):
    search, _ = indexed
    assert search.search("kubernetes") == []


def test_empty_query_returns_empty(indexed):
    search, _ = indexed
    assert search.search("") == []
    assert search.search("   ") == []


def test_stopword_only_query_returns_empty(indexed):
    search, _ = indexed
    assert search.search("the and for") == []


def test_k_limits_hits_and_zero_raises(indexed):
    search, _ = indexed
    assert len(search.search("pool", k=1)) == 1
    with pytest.raises(ValueError):
        search.search("pool", k=0)


def test_scores_are_descending(indexed):
    search, _ = indexed
    scores = [hit.score for hit in search.search(PHRASE, k=10)]
    assert len(scores) >= 2
    assert scores == sorted(scores, reverse=True)


def test_format_hits_renders_citation_and_three_decimals(indexed):
    search, _ = indexed
    hits = search.search(PHRASE)
    lines = format_hits(hits).split("\n")

    assert len(lines) == len(hits)
    for line, hit in zip(lines, hits):
        assert line == f"{hit.citation} | {hit.score:.3f}"
        assert re.fullmatch(r"\S+:\d+-\d+ \| \d+\.\d{3}", line)
    assert format_hits([]) == ""


def test_index_terms_counts_chunks_per_term(indexed):
    search, _ = indexed
    terms = search.index_terms()
    assert terms["pool"] >= 2
