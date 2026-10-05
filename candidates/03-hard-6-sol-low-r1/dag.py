"""Concurrent execution of validated dependency graphs."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import deque
import heapq


def run_graph(tasks, max_workers=2):
    """Run a DAG, recording failures and skipping their descendants."""
    if type(max_workers) is not int or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    deps = {}
    fns = {}
    children = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        parents = task["deps"]
        if not isinstance(parents, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        if any(not isinstance(parent, str) or not parent for parent in parents):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(parents) != len(set(parents)):
            raise ValueError("duplicate dependency")
        deps[task_id] = tuple(parents)
        fns[task_id] = task["fn"]
        children[task_id] = []

    for task_id, parents in deps.items():
        for parent in parents:
            if parent not in deps or parent == task_id:
                raise ValueError("unknown or self dependency")
            children[parent].append(task_id)

    # Validate cycles before any function can be submitted to the pool.
    remaining = {task_id: len(parents) for task_id, parents in deps.items()}
    queue = deque(task_id for task_id, count in remaining.items() if count == 0)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in children[task_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    if visited != len(deps):
        raise ValueError("dependency graph contains a cycle")

    remaining = {task_id: len(parents) for task_id, parents in deps.items()}
    blocked = {task_id: False for task_id in deps}
    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    results = {}

    def finish(task_id, result):
        """Resolve a finished task and iteratively propagate skips."""
        pending = deque([(task_id, result)])
        while pending:
            current, outcome = pending.popleft()
            results[current] = outcome
            failed = outcome["status"] != "completed"
            for child in children[current]:
                remaining[child] -= 1
                if failed:
                    blocked[child] = True
                if remaining[child] == 0:
                    if blocked[child]:
                        pending.append((child, {"status": "skipped"}))
                    else:
                        heapq.heappush(ready, child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                running[executor.submit(fns[task_id])] = task_id
            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = running.pop(future)
                    try:
                        result = {"status": "completed", "value": future.result()}
                    except Exception as exc:
                        result = {"status": "failed", "error": str(exc)}
                    finish(task_id, result)

    return {task_id: results[task_id] for task_id in sorted(deps)}
