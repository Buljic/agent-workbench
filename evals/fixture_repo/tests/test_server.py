from app import server
from app.server import page, validate, worker


def test_fetch_json_passes_timeout(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": []}

    def fake_get(url, timeout=None):
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(server.httpx, "get", fake_get)
    assert server.fetch_json("http://queue.test/items", timeout=5) == {"items": []}
    assert seen["timeout"] == 5


def test_page_first_slice():
    # pins the current slice, which returns one item less than page_size
    assert page(list(range(10)), 0, 3) == [0, 1]


def test_validate_requires_id_and_payload():
    assert validate({"id": "a", "payload": 1})
    assert not validate({"id": "a"})


def test_worker_stores_single_item():
    server.RESULTS.clear()
    batch = [{"id": "a", "payload": "xyz"}]
    worker(batch)
    assert server.RESULTS["a"] == {"id": "a", "size": 3, "status": "done"}
    assert batch == []
