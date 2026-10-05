"""Concurrent execution of validated dependency graphs."""

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Run each task after its dependencies succeed, using at most max_workers."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    children = {task_id: [] for task_id in task_ids}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict):
            raise ValueError("each task must be a dictionary")
        deps = task.get("deps")
        fn = task.get("fn")
        if not isinstance(deps, list) or not callable(fn):
            raise ValueError("each task needs a dependency list and callable fn")
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep == task_id:
                raise ValueError("a task cannot depend on itself")
            if dep not in children:
                raise ValueError("unknown dependency")
            seen.add(dep)
            children[dep].append(task_id)
        dependencies[task_id] = tuple(deps)
        functions[task_id] = fn

    # Kahn's algorithm checks the whole graph without recursive traversal.
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    degree = remaining.copy()
    queue = deque(task_id for task_id in task_ids if degree[task_id] == 0)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in children[task_id]:
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    results = {}
    blocked = {task_id: False for task_id in task_ids}
    ready = [task_id for task_id in task_ids if remaining[task_id] == 0]
    heapify(ready)

    def settle(task_id, succeeded):
        """Propagate a finished task and iteratively skip blocked descendants."""
        finished = deque([(task_id, succeeded)])
        while finished:
            parent, parent_succeeded = finished.popleft()
            for child in children[parent]:
                remaining[child] -= 1
                if not parent_succeeded:
                    blocked[child] = True
                if remaining[child] == 0:
                    if blocked[child]:
                        results[child] = {"status": "skipped"}
                        finished.append((child, False))
                    else:
                        heappush(ready, child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
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
                    settle(task_id, False)
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    settle(task_id, True)

    return {task_id: results[task_id] for task_id in sorted(task_ids)}
