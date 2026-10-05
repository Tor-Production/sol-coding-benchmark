"""Execute dependency graphs with a bounded number of threads."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Validate and execute a DAG, returning results in task-ID order."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    # Keep our own graph so callers' dependency lists are never changed.
    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task needs deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        if any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(deps) != len(set(deps)):
            raise ValueError("duplicate dependency")
        dependencies[task_id] = tuple(deps)
        functions[task_id] = task["fn"]

    children = {task_id: [] for task_id in dependencies}
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in dependencies:
                raise ValueError("unknown dependency")
            if dep == task_id:
                raise ValueError("self dependency")
            children[dep].append(task_id)

    # Kahn's algorithm checks the whole graph without recursion.
    counts = remaining.copy()
    queue = deque(task_id for task_id, count in counts.items() if count == 0)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in children[task_id]:
            counts[child] -= 1
            if counts[child] == 0:
                queue.append(child)
    if visited != len(dependencies):
        raise ValueError("dependency cycle")

    if not dependencies:
        return {}

    def invoke(fn):
        try:
            return {"status": "completed", "value": fn()}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    blocked = {task_id: False for task_id in dependencies}
    results = {}

    def finish(task_id, result):
        # A skipped node propagates its blocked state just like a failed node.
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
                running[executor.submit(invoke, functions[task_id])] = task_id
            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    finish(running.pop(future), future.result())

    return {task_id: results[task_id] for task_id in sorted(results)}
