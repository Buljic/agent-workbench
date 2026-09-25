import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

TOKEN = re.compile(r"[a-z0-9_]+")
STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "when",
        "then", "are", "was", "were", "not", "you", "can", "its",
    }
)


@dataclass(frozen=True)
class ChunkHit:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float

    @property
    def citation(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


def tokenize(text: str) -> list[str]:
    return [tok for tok in TOKEN.findall(text.lower()) if len(tok) > 1 and tok not in STOPWORDS]


class _Doc(NamedTuple):
    path: str
    start_line: int
    end_line: int
    text: str
    counts: Counter
    length: int


class ChunkSearch:
    def __init__(self, conn: sqlite3.Connection, k1: float = 1.2, b: float = 0.75) -> None:
        self.conn = conn
        self.k1 = k1
        self.b = b

    def search(self, query: str, k: int = 5) -> list[ChunkHit]:
        if k < 1:
            raise ValueError("k must be at least 1")
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms:
            return []

        docs = self._load()
        if not docs:
            return []

        n = len(docs)
        avg_len = sum(doc.length for doc in docs) / n
        idf = {}
        for term in terms:
            df = sum(1 for doc in docs if term in doc.counts)
            idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))

        hits = []
        for doc in docs:
            score = 0.0
            for term in terms:
                tf = doc.counts.get(term, 0)
                if not tf:
                    continue
                norm = tf + self.k1 * (1 - self.b + self.b * doc.length / avg_len)
                score += idf[term] * tf * (self.k1 + 1) / norm
            if score > 0:
                hits.append(ChunkHit(doc.path, doc.start_line, doc.end_line, doc.text, score))

        hits.sort(key=lambda hit: (-hit.score, hit.path, hit.start_line))
        return hits[:k]

    def index_terms(self) -> dict[str, int]:
        df: Counter[str] = Counter()
        for doc in self._load():
            df.update(doc.counts.keys())
        return dict(sorted(df.items()))

    def _load(self) -> list[_Doc]:
        # read fresh on every call so the index can change between searches
        rows = self.conn.execute("SELECT path, start_line, end_line, text FROM chunks").fetchall()
        docs = []
        for path, start, end, text in rows:
            tokens = tokenize(text)
            docs.append(_Doc(path, start, end, text, Counter(tokens), len(tokens)))
        return docs


def format_hits(hits: list[ChunkHit]) -> str:
    return "\n".join(f"{hit.citation} | {hit.score:.3f}" for hit in hits)
