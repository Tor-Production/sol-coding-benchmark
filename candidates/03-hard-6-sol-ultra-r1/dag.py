"""Execute dependency graphs with bounded thread concurrency."""

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Validate and execute a DAG, returning results in task-ID order."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Copy the graph's structure before any function is allowed to run.
    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, Mapping) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        if any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(deps) != len(set(deps)):
            raise ValueError("duplicate dependency")
        dependencies[task_id] = tuple(deps)
        functions[task_id] = task["fn"]

    dependents = {task_id: [] for task_id in dependencies}
    remaining = {}
    for task_id, deps in dependencies.items():
        remaining[task_id] = len(deps)
        for dep in deps:
            if dep not in dependencies:
                raise ValueError("unknown dependency")
            if dep == task_id:
                raise ValueError("self dependency")
            dependents[dep].append(task_id)

    # Kahn's algorithm detects cycles without following Python's call stack.
    indegree = remaining.copy()
    roots = deque(task_id for task_id, count in indegree.items() if count == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for child in dependents[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                roots.append(child)
    if visited != len(dependencies):
        raise ValueError("dependency cycle")

    if not dependencies:
        return {}

    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapify(ready)
    outcomes = {}
    running = {}

    def skip_descendants(task_id):
        pending = deque([task_id])
        while pending:
            parent = pending.popleft()
            for child in dependents[parent]:
                if child not in outcomes:
                    outcomes[child] = {"status": "skipped"}
                    pending.append(child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                if task_id not in outcomes:
                    running[executor.submit(functions[task_id])] = task_id

            if not running:
                continue
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    outcomes[task_id] = {"status": "failed", "error": str(exc)}
                    skip_descendants(task_id)
                else:
                    outcomes[task_id] = {"status": "completed", "value": value}
                    for child in dependents[task_id]:
                        if child not in outcomes:
                            remaining[child] -= 1
                            if remaining[child] == 0:
                                heappush(ready, child)

    return {task_id: outcomes[task_id] for task_id in sorted(dependencies)}
