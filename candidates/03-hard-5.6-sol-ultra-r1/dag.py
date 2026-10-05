"""Concurrent execution of dependency graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def _validate(tasks, max_workers):
    """Validate and take a shallow snapshot of a task graph."""
    if (isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers <= 0):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    dependencies = {}
    functions = {}

    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict):
            raise ValueError("each task must be a dictionary")
        if "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")

        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list):
            raise ValueError("task deps must be a list")
        if not callable(fn):
            raise ValueError("task fn must be callable")

        seen = set()
        copied_deps = []
        for dependency in deps:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            if dependency == task_id:
                raise ValueError("a task cannot depend on itself")
            seen.add(dependency)
            copied_deps.append(dependency)

        dependencies[task_id] = tuple(copied_deps)
        functions[task_id] = fn

    task_ids = sorted(dependencies)
    known_ids = set(dependencies)
    children = {task_id: [] for task_id in task_ids}
    indegree = {}

    for task_id in task_ids:
        deps = dependencies[task_id]
        for dependency in deps:
            if dependency not in known_ids:
                raise ValueError("unknown dependency")
            children[dependency].append(task_id)
        indegree[task_id] = len(deps)

    # Kahn's algorithm validates cycles without recursion, including on very
    # deep graphs.
    roots = [task_id for task_id in task_ids if indegree[task_id] == 0]
    heapq.heapify(roots)
    visited = 0
    while roots:
        task_id = heapq.heappop(roots)
        visited += 1
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(roots, child)

    if visited != len(task_ids):
        raise ValueError("task graph contains a cycle")

    return task_ids, dependencies, functions, children


def run_graph(tasks, max_workers=2):
    """Execute a validated DAG with bounded concurrent workers."""
    task_ids, dependencies, functions, children = _validate(tasks, max_workers)
    if not task_ids:
        return {}

    remaining = {
        task_id: len(dependencies[task_id]) for task_id in task_ids
    }
    has_failed_dependency = {task_id: False for task_id in task_ids}
    ready = [task_id for task_id in task_ids if remaining[task_id] == 0]
    heapq.heapify(ready)
    results = {}
    running = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while len(results) < len(task_ids):
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            # A validated DAG always has either runnable work or running work.
            done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
            notifications = deque()

            # Sorting only makes simultaneous completions deterministic; it
            # does not impose a barrier between unrelated running tasks.
            completed = sorted(
                ((running.pop(future), future) for future in done),
                key=lambda item: item[0],
            )
            for task_id, future in completed:
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {
                        "status": "failed",
                        "error": str(exc),
                    }
                    notifications.append((task_id, False))
                else:
                    results[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    notifications.append((task_id, True))

            # Resolve newly unblocked tasks and cascade skips iteratively so a
            # failure can skip a chain of arbitrary length.
            while notifications:
                task_id, succeeded = notifications.popleft()
                for child in children[task_id]:
                    remaining[child] -= 1
                    if not succeeded:
                        has_failed_dependency[child] = True
                    if remaining[child] != 0:
                        continue
                    if has_failed_dependency[child]:
                        results[child] = {"status": "skipped"}
                        notifications.append((child, False))
                    else:
                        heapq.heappush(ready, child)

    return {task_id: results[task_id] for task_id in task_ids}
