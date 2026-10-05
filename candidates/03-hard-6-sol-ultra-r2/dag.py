"""Run dependency graphs with a bounded pool of worker threads."""

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

    task_ids = set(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")

    sorted_ids = sorted(task_ids)
    functions = {}
    dependencies = {}
    children = {task_id: [] for task_id in task_ids}
    for task_id in sorted_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary with deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list):
            raise ValueError("deps must be a list")
        fn = task["fn"]
        if not callable(fn):
            raise ValueError("fn must be callable")

        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep == task_id:
                raise ValueError("self-dependency")
            if dep not in task_ids:
                raise ValueError("unknown dependency")
            seen.add(dep)
            children[dep].append(task_id)
        dependencies[task_id] = len(deps)
        functions[task_id] = fn

    # Kahn's algorithm checks the whole graph without recursive traversal.
    unchecked = dependencies.copy()
    roots = [task_id for task_id in sorted_ids if unchecked[task_id] == 0]
    heapify(roots)
    visited = 0
    while roots:
        task_id = heappop(roots)
        visited += 1
        for child in children[task_id]:
            unchecked[child] -= 1
            if unchecked[child] == 0:
                heappush(roots, child)
    if visited != len(task_ids):
        raise ValueError("dependency cycle")

    ready = [task_id for task_id in sorted_ids if dependencies[task_id] == 0]
    heapify(ready)
    remaining = dependencies.copy()
    blocked = {task_id: False for task_id in task_ids}
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                running[pool.submit(functions[task_id])] = task_id

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            finalized = deque()
            for future in done:
                task_id = running.pop(future)
                try:
                    results[task_id] = {"status": "completed", "value": future.result()}
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                finalized.append(task_id)

            # A skipped task counts as a failed prerequisite for every child.
            while finalized:
                task_id = finalized.popleft()
                succeeded = results[task_id]["status"] == "completed"
                for child in children[task_id]:
                    remaining[child] -= 1
                    if not succeeded:
                        blocked[child] = True
                    if remaining[child] == 0:
                        if blocked[child]:
                            results[child] = {"status": "skipped"}
                            finalized.append(child)
                        else:
                            heappush(ready, child)

    return {task_id: results[task_id] for task_id in sorted_ids}
