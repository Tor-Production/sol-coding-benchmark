"""Concurrent execution of validated dependency graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Execute *tasks* once their dependencies have succeeded.

    Validation and cycle detection are completed before an executor is
    created, so an invalid graph cannot cause even a root task to run.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    task_ids = set(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary with deps and fn")

        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list):
            raise ValueError("task deps must be a list")
        if not callable(fn):
            raise ValueError("task fn must be callable")

        copied_deps = []
        seen_deps = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen_deps:
                raise ValueError("task dependencies must not contain duplicates")
            seen_deps.add(dep)
            copied_deps.append(dep)

        dependencies[task_id] = copied_deps
        functions[task_id] = fn

    dependents = {task_id: [] for task_id in task_ids}
    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in task_ids:
                raise ValueError("task has an unknown dependency")
            if dep == task_id:
                raise ValueError("task must not depend on itself")
            dependents[dep].append(task_id)

    # Kahn's algorithm validates cycles without using the Python call stack.
    indegree = {task_id: len(dependencies[task_id]) for task_id in task_ids}
    roots = deque(task_id for task_id in task_ids if indegree[task_id] == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for child in dependents[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                roots.append(child)
    if visited != len(task_ids):
        raise ValueError("task graph contains a cycle")

    if not task_ids:
        return {}

    # A heap makes the choice among simultaneously ready tasks stable.  Result
    # insertion order is established separately after all work is complete.
    remaining = {
        task_id: len(dependencies[task_id]) for task_id in task_ids
    }
    blocked = {task_id: False for task_id in task_ids}
    ready = [task_id for task_id in task_ids if remaining[task_id] == 0]
    heapq.heapify(ready)
    results = {}
    running = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            if not running:
                break

            done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
            finished = sorted((running.pop(future), future) for future in done)
            terminal = deque()

            for task_id, future in finished:
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {
                        "status": "failed",
                        "error": str(exc),
                    }
                    terminal.append((task_id, True))
                else:
                    results[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    terminal.append((task_id, False))

            # Resolve newly eligible tasks and skipped descendants iteratively.
            # A skipped task behaves like a failed dependency for its children.
            while terminal:
                task_id, did_not_succeed = terminal.popleft()
                for child in dependents[task_id]:
                    remaining[child] -= 1
                    if did_not_succeed:
                        blocked[child] = True
                    if remaining[child] == 0:
                        if blocked[child]:
                            results[child] = {"status": "skipped"}
                            terminal.append((child, True))
                        else:
                            heapq.heappush(ready, child)

    return {task_id: results[task_id] for task_id in sorted(task_ids)}
