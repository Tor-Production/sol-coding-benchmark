"""Concurrent execution of validated dependency graphs."""

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Run a DAG of zero-argument functions with bounded concurrency.

    The graph is validated in full before any function is submitted. A failed
    task prevents each of its descendants from running, while independent work
    continues.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    task_ids = list(tasks)
    for task_id in task_ids:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    children = {task_id: [] for task_id in task_ids}
    dependency_counts = {}
    functions = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, Mapping) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must have deps and fn")
        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list) or not callable(fn):
            raise ValueError("deps must be a list and fn must be callable")

        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep not in children:
                raise ValueError("unknown dependency")
            if dep == task_id:
                raise ValueError("self-dependency")
            seen.add(dep)
            children[dep].append(task_id)

        dependency_counts[task_id] = len(deps)
        functions[task_id] = fn

    # Kahn's algorithm checks cycles without using the Python call stack.
    unresolved = dependency_counts.copy()
    roots = deque(task_id for task_id in task_ids if unresolved[task_id] == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for child in children[task_id]:
            unresolved[child] -= 1
            if unresolved[child] == 0:
                roots.append(child)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")
    if not task_ids:
        return {}

    remaining = dependency_counts.copy()
    failed_dependency = {task_id: False for task_id in task_ids}
    ready = deque(sorted(task_id for task_id in task_ids if remaining[task_id] == 0))
    results = {}

    def finish(task_id, succeeded):
        """Propagate a terminal result, including any newly skipped tasks."""
        terminal = deque([(task_id, succeeded)])
        while terminal:
            parent, parent_succeeded = terminal.popleft()
            for child in children[parent]:
                remaining[child] -= 1
                if not parent_succeeded:
                    failed_dependency[child] = True
                if remaining[child] == 0:
                    if failed_dependency[child]:
                        results[child] = {"status": "skipped"}
                        terminal.append((child, False))
                    else:
                        ready.appendleft(child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            if not running:
                continue
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    finish(task_id, False)
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    finish(task_id, True)

    return {task_id: results[task_id] for task_id in sorted(task_ids)}
