"""Concurrent execution of validated dependency graphs."""

from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Execute a DAG with bounded concurrency and return ordered task results."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    dependencies = {}
    functions = {}
    children = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must have deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        if any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(deps) != len(set(deps)):
            raise ValueError("duplicate dependency")
        dependencies[task_id] = tuple(deps)
        functions[task_id] = task["fn"]
        children[task_id] = []

    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in dependencies or dep == task_id:
                raise ValueError("unknown or self dependency")
            children[dep].append(task_id)

    # Kahn's algorithm validates the whole graph without recursive traversal.
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    roots = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(roots)
    visited = 0
    while roots:
        task_id = heapq.heappop(roots)
        visited += 1
        for child in children[task_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                heapq.heappush(roots, child)
    if visited != len(dependencies):
        raise ValueError("dependency graph contains a cycle")

    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    failed_dependency = {task_id: False for task_id in dependencies}
    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    results = {}

    def finish(task_id, result):
        """Propagate a result, including skips, through newly resolved nodes."""
        pending = [(task_id, result)]
        while pending:
            done_id, done_result = pending.pop()
            results[done_id] = done_result
            succeeded = done_result["status"] == "completed"
            for child in children[done_id]:
                remaining[child] -= 1
                if not succeeded:
                    failed_dependency[child] = True
                if remaining[child] == 0:
                    if failed_dependency[child]:
                        pending.append((child, {"status": "skipped"}))
                    else:
                        heapq.heappush(ready, child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                running[executor.submit(functions[task_id])] = task_id
            if running:
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    task_id = running.pop(future)
                    try:
                        result = {"status": "completed", "value": future.result()}
                    except Exception as exc:
                        result = {"status": "failed", "error": str(exc)}
                    finish(task_id, result)

    return {task_id: results[task_id] for task_id in sorted(results)}
