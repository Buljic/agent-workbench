from workbench.retrieval import RepoIndex
from workbench.retrieval.search import ChunkSearch
from workbench.tools.registry import ToolResult


class SearchChunksTool:
    """Chunk-level search over the indexed repository, with line citations.

    The index is incremental: files whose hash is unchanged are skipped, so
    repeated calls only pay for what changed.
    """

    name = "search_chunks"
    description = (
        "search indexed code and documentation chunks, returns ranked excerpts "
        "with path:start-end citations"
    )

    def __init__(self, root, conn_factory=None):
        self._root = root
        self._conn_factory = conn_factory
        self._conn = None

    def _connection(self):
        if self._conn is None:
            if self._conn_factory is not None:
                self._conn = self._conn_factory()
            else:
                import sqlite3

                self._conn = sqlite3.connect(":memory:")
                self._conn.row_factory = sqlite3.Row
        return self._conn

    def run(self, arguments):
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        k = int(arguments.get("k", 5))
        conn = self._connection()
        index = RepoIndex(conn, self._root)
        index.index()
        hits = ChunkSearch(conn).search(query, k=k)
        if not hits:
            return ToolResult(tool=self.name, output="no matches", citations=[])
        citations = [hit.citation for hit in hits]
        lines = []
        for hit in hits:
            lines.append(f"{hit.citation} | {hit.score:.3f}")
            for line in hit.text.splitlines()[:20]:
                lines.append(f"    {line}")
        return ToolResult(tool=self.name, output="\n".join(lines), citations=citations)
