"""Run validated dependency graphs with a bounded pool of threads."""

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Execute tasks after their dependencies succeed.

    The graph is fully checked before any task function is submitted. A failed
    task blocks all of its descendants, while independent tasks keep running.
    """
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    for task_id in tasks:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    ids = set(tasks)
    functions = {}
    dependents = {task_id: [] for task_id in ids}
    indegree = {}
    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary with deps and fn")
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
                raise ValueError("self-dependency")
            if dep not in ids:
                raise ValueError("unknown dependency")
            seen.add(dep)
            dependents[dep].append(task_id)
        indegree[task_id] = len(deps)
        functions[task_id] = task["fn"]

    # Kahn's algorithm detects cycles without recursive traversal.
    remaining = indegree.copy()
    visit = deque(task_id for task_id, degree in remaining.items() if degree == 0)
    visited = 0
    while visit:
        parent = visit.popleft()
        visited += 1
        for child in dependents[parent]:
            remaining[child] -= 1
            if remaining[child] == 0:
                visit.append(child)
    if visited != len(ids):
        raise ValueError("dependency graph contains a cycle")
    if not ids:
        return {}

    remaining = indegree.copy()
    blocked = {task_id: False for task_id in ids}
    ready = [task_id for task_id, degree in remaining.items() if degree == 0]
    heapify(ready)
    results = {}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(ids))) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                running[executor.submit(functions[task_id])] = task_id

            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            resolved = deque()
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    resolved.append((task_id, False))
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    resolved.append((task_id, True))

            # A skipped task resolves its outgoing edges just like a failed one.
            # This queue also handles long chains without recursion.
            while resolved:
                parent, succeeded = resolved.popleft()
                for child in dependents[parent]:
                    remaining[child] -= 1
                    if not succeeded:
                        blocked[child] = True
                    if remaining[child] == 0:
                        if blocked[child]:
                            results[child] = {"status": "skipped"}
                            resolved.append((child, False))
                        else:
                            heappush(ready, child)

    return {task_id: results[task_id] for task_id in sorted(ids)}
