"""Execute dependency graphs with a bounded pool of threads."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Validate and execute tasks, returning results in task-ID order."""
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    children = {}
    dependencies = {}
    for task_id in tasks:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        children[task_id] = []

    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must have deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep == task_id:
                raise ValueError("task cannot depend on itself")
            if dep not in tasks:
                raise ValueError("unknown dependency")
            seen.add(dep)
            children[dep].append(task_id)
        dependencies[task_id] = len(deps)

    # Kahn's algorithm validates cycles without following Python call stacks.
    remaining = dependencies.copy()
    roots = deque(task_id for task_id, count in remaining.items() if count == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for child in children[task_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                roots.append(child)
    if visited != len(tasks):
        raise ValueError("dependency graph contains a cycle")

    if not tasks:
        return {}

    ready = [task_id for task_id, count in dependencies.items() if count == 0]
    heapify(ready)
    pending = dependencies.copy()
    blocked = set()
    results = {}

    def finish(task_id, result):
        """Propagate a terminal result through newly resolved descendants."""
        terminal = deque([(task_id, result)])
        while terminal:
            current, outcome = terminal.popleft()
            results[current] = outcome
            for child in children[current]:
                pending[child] -= 1
                if outcome["status"] != "completed":
                    blocked.add(child)
                if pending[child] == 0:
                    if child in blocked:
                        terminal.append((child, {"status": "skipped"}))
                    else:
                        heappush(ready, child)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                running[pool.submit(tasks[task_id]["fn"])] = task_id
            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = running.pop(future)
                    try:
                        result = {"status": "completed", "value": future.result()}
                    except Exception as exc:
                        result = {"status": "failed", "error": str(exc)}
                    finish(task_id, result)

    return {task_id: results[task_id] for task_id in sorted(tasks)}
