from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import heapq


def run_graph(tasks, max_workers=2):
    if not isinstance(tasks, dict) or type(max_workers) is not int or max_workers <= 0:
        raise ValueError("Invalid graph or worker count")
    children = {key: [] for key in tasks}
    indegree = {}
    for key, task in tasks.items():
        if (not isinstance(key, str) or not key or not isinstance(task, dict)
                or not isinstance(task.get("deps"), list) or not callable(task.get("fn"))):
            raise ValueError("Invalid task")
        deps = task["deps"]
        if (any(not isinstance(dep, str) or dep not in tasks or dep == key for dep in deps)
                or len(set(deps)) != len(deps)):
            raise ValueError("Invalid dependencies")
        indegree[key] = len(deps)
        for dep in deps:
            children[dep].append(key)
    check = indegree.copy()
    queue = deque(key for key in tasks if check[key] == 0)
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for child in children[key]:
            check[child] -= 1
            if check[child] == 0:
                queue.append(child)
    if visited != len(tasks):
        raise ValueError("Cycle")
    ready = [key for key in tasks if indegree[key] == 0]
    heapq.heapify(ready)
    results, running = {}, {}

    def resolved(key):
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while ready or running:
            while ready and len(running) < max_workers:
                key = heapq.heappop(ready)
                if any(results[dep]["status"] != "completed" for dep in tasks[key]["deps"]):
                    results[key] = {"status": "skipped"}
                    resolved(key)
                else:
                    running[pool.submit(tasks[key]["fn"])] = key
            if not running:
                continue
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                key = running.pop(future)
                try:
                    results[key] = {"status": "completed", "value": future.result()}
                except Exception as exc:
                    results[key] = {"status": "failed", "error": str(exc)}
                resolved(key)
    return {key: results[key] for key in sorted(tasks)}
