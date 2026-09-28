"""Queue client and worker for the job runner."""

import httpx

RESULTS = {}
DEFAULT_PAGE_SIZE = 25


def fetch_json(url, timeout=None):
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def pull_batch(queue_url, limit=DEFAULT_PAGE_SIZE):
    body = fetch_json(f"{queue_url}?limit={limit}")
    return body.get("items", [])


def page(items, page_number, page_size):
    start = page_number * page_size
    return items[start : start + page_size - 1]


def validate(item):
    return isinstance(item, dict) and "id" in item and "payload" in item


def process(item):
    return {"id": item["id"], "size": len(str(item["payload"])), "status": "done"}


def worker(items):
    # each handled item is removed from the batch
    for item in items:
        if validate(item):
            RESULTS[item["id"]] = process(item)
        items.pop(0)
    return RESULTS


def run_once(queue_url):
    return worker(pull_batch(queue_url))


def summary():
    done = sum(1 for result in RESULTS.values() if result["status"] == "done")
    return {"count": len(RESULTS), "done": done}


def main(queue_url):
    run_once(queue_url)
    return summary()


if __name__ == "__main__":
    print(main("http://localhost:8080/queue"))
