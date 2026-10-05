"""Concurrent execution of validated dependency graphs."""

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Run a DAG, returning one outcome per task in sorted ID order."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    children = {}
    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        if any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(deps) != len(set(deps)):
            raise ValueError("duplicate dependency")
        dependencies[task_id] = deps[:]
        functions[task_id] = task["fn"]
        children[task_id] = []

    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in children or dep == task_id:
                raise ValueError("unknown or self dependency")
            children[dep].append(task_id)

    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    ready = deque(task_id for task_id, count in remaining.items() if count == 0)
    visited = 0
    while ready:
        task_id = ready.popleft()
        visited += 1
        for child in children[task_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    if visited != len(tasks):
        raise ValueError("dependency graph contains a cycle")

    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    ready = deque(task_id for task_id, count in remaining.items() if count == 0)
    blocked = set()
    results = {}

    def finish(task_id, result):
        """Propagate a completed or skipped outcome without recursion."""
        pending = deque([(task_id, result)])
        while pending:
            current, outcome = pending.popleft()
            results[current] = outcome
            for child in children[current]:
                remaining[child] -= 1
                if outcome["status"] != "completed":
                    blocked.add(child)
                if remaining[child] == 0:
                    if child in blocked:
                        pending.append((child, {"status": "skipped"}))
                    else:
                        ready.append(child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id
            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = running.pop(future)
                    try:
                        outcome = {"status": "completed", "value": future.result()}
                    except Exception as exc:
                        outcome = {"status": "failed", "error": str(exc)}
                    finish(task_id, outcome)

    return {task_id: results[task_id] for task_id in sorted(results)}
