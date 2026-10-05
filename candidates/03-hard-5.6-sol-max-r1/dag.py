"""Concurrent execution of validated dependency graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def _validated_graph(tasks, max_workers):
    """Return immutable task data and adjacency lists after full validation."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    task_ids = list(tasks)
    for task_id in task_ids:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")

        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list):
            raise ValueError("deps must be a list")
        if not callable(fn):
            raise ValueError("fn must be callable")

        for dependency in deps:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
        if len(deps) != len(set(deps)):
            raise ValueError("duplicate dependency")
        if task_id in deps:
            raise ValueError("a task cannot depend on itself")
        for dependency in deps:
            if dependency not in tasks:
                raise ValueError("unknown dependency")

        # Copy the list so execution never depends on, or changes, caller data.
        dependencies[task_id] = tuple(deps)
        functions[task_id] = fn

    dependents = {task_id: [] for task_id in task_ids}
    indegree = {}
    for task_id, deps in dependencies.items():
        indegree[task_id] = len(deps)
        for dependency in deps:
            dependents[dependency].append(task_id)

    # Kahn's algorithm validates cycles without relying on recursion depth.
    roots = [task_id for task_id, count in indegree.items() if count == 0]
    heapq.heapify(roots)
    seen = 0
    validation_indegree = indegree.copy()
    while roots:
        task_id = heapq.heappop(roots)
        seen += 1
        for dependent in dependents[task_id]:
            validation_indegree[dependent] -= 1
            if validation_indegree[dependent] == 0:
                heapq.heappush(roots, dependent)
    if seen != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    for children in dependents.values():
        children.sort()
    return sorted(task_ids), dependencies, functions, dependents


def run_graph(tasks, max_workers=2):
    """Execute a fully validated DAG with bounded concurrent workers."""
    task_ids, dependencies, functions, dependents = _validated_graph(
        tasks, max_workers
    )
    if not task_ids:
        return {}

    unresolved = {
        task_id: len(dependencies[task_id]) for task_id in task_ids
    }
    blocked = {task_id: False for task_id in task_ids}
    ready = [task_id for task_id in task_ids if unresolved[task_id] == 0]
    heapq.heapify(ready)
    outcomes = {}
    running = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            if not running:
                break

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            newly_finished = deque()
            for future in sorted(done, key=lambda item: running[item]):
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exception:
                    outcomes[task_id] = {
                        "status": "failed",
                        "error": str(exception),
                    }
                    newly_finished.append((task_id, False))
                else:
                    outcomes[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    newly_finished.append((task_id, True))

            # A skipped task acts as an unsuccessful dependency for its own
            # children.  This iterative propagation also handles long chains.
            while newly_finished:
                task_id, successful = newly_finished.popleft()
                for dependent in dependents[task_id]:
                    unresolved[dependent] -= 1
                    if not successful:
                        blocked[dependent] = True
                    if unresolved[dependent] != 0:
                        continue
                    if blocked[dependent]:
                        outcomes[dependent] = {"status": "skipped"}
                        newly_finished.append((dependent, False))
                    else:
                        heapq.heappush(ready, dependent)

    return {task_id: outcomes[task_id] for task_id in task_ids}
