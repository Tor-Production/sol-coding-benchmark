"""Concurrent execution of validated dependency graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heappop, heappush


def _validated_graph(tasks, max_workers):
    """Validate and take a shallow snapshot of the graph's relevant fields."""
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

    all_ids = set(task_ids)
    dependencies = {}
    functions = {}

    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary with deps and fn")

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
            if dependency not in all_ids:
                raise ValueError("unknown dependency")
            seen.add(dependency)
            copied_deps.append(dependency)

        dependencies[task_id] = tuple(copied_deps)
        functions[task_id] = fn

    dependents = {task_id: [] for task_id in task_ids}
    indegree = {}
    for task_id, deps in dependencies.items():
        indegree[task_id] = len(deps)
        for dependency in deps:
            dependents[dependency].append(task_id)

    # Kahn's algorithm validates acyclicity without recursion, including for
    # graphs whose longest path exceeds Python's recursion limit.
    roots = deque(task_id for task_id in task_ids if indegree[task_id] == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for dependent in dependents[task_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                roots.append(dependent)
    if visited != len(task_ids):
        raise ValueError("task graph contains a cycle")

    return sorted(task_ids), dependencies, functions, dependents


def run_graph(tasks, max_workers=2):
    """Validate and execute a dependency graph with bounded concurrency."""
    task_ids, dependencies, functions, dependents = _validated_graph(
        tasks, max_workers
    )
    if not task_ids:
        return {}

    remaining = {
        task_id: len(dependencies[task_id]) for task_id in task_ids
    }
    has_failed_dependency = {task_id: False for task_id in task_ids}
    ready = []
    for task_id in task_ids:
        if remaining[task_id] == 0:
            heappush(ready, task_id)

    outcomes = {}

    def resolve(start_task, successful):
        """Iteratively propagate one terminal result through the graph."""
        resolved = deque([(start_task, successful)])
        while resolved:
            task_id, task_succeeded = resolved.popleft()
            for dependent in dependents[task_id]:
                remaining[dependent] -= 1
                if not task_succeeded:
                    has_failed_dependency[dependent] = True
                if remaining[dependent] != 0:
                    continue
                if has_failed_dependency[dependent]:
                    outcomes[dependent] = {"status": "skipped"}
                    resolved.append((dependent, False))
                else:
                    heappush(ready, dependent)

    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            # Keeping the executor queue empty is important: a dependent that
            # becomes ready can use the very next free worker slot.
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            if not running:
                continue

            completed, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in completed:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exception:
                    outcomes[task_id] = {
                        "status": "failed",
                        "error": str(exception),
                    }
                    resolve(task_id, False)
                else:
                    outcomes[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    resolve(task_id, True)

    return {task_id: outcomes[task_id] for task_id in task_ids}
