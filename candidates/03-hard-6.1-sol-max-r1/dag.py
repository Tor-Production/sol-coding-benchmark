from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def _validate_graph(tasks, max_workers):
    """Snapshot the graph and check it without calling task functions."""
    if (
        not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or max_workers <= 0
    ):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")
    task_ids.sort()

    functions = {}
    dependents = {task_id: [] for task_id in task_ids}
    dependency_counts = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        if not isinstance(task["deps"], list):
            raise ValueError("deps must be a list")
        if not callable(task["fn"]):
            raise ValueError("fn must be callable")

        dependencies = tuple(task["deps"])
        seen = set()
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            if dependency not in dependents:
                raise ValueError("unknown dependency")
            if dependency == task_id:
                raise ValueError("a task cannot depend on itself")
            seen.add(dependency)
            dependents[dependency].append(task_id)
        functions[task_id] = task["fn"]
        dependency_counts[task_id] = len(dependencies)

    # Kahn's algorithm also detects cycles in disconnected parts of the graph.
    remaining = dependency_counts.copy()
    roots = [task_id for task_id in task_ids if remaining[task_id] == 0]
    ready = deque(roots)
    visited = 0
    while ready:
        task_id = ready.popleft()
        visited += 1
        for dependent in dependents[task_id]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    return task_ids, functions, dependents, dependency_counts, roots


def run_graph(tasks, max_workers=2):
    """Execute a validated DAG with bounded concurrent workers."""
    task_ids, functions, dependents, remaining, roots = _validate_graph(
        tasks, max_workers
    )
    if not task_ids:
        return {}

    results = {}
    ready = deque(roots)
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            completed, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in completed:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as error:
                    results[task_id] = {"status": "failed", "error": str(error)}
                    # A failed parent blocks every descendant, including joins
                    # with successful parents. Iterate to support deep graphs.
                    blocked = list(dependents[task_id])
                    while blocked:
                        dependent = blocked.pop()
                        if dependent not in results:
                            results[dependent] = {"status": "skipped"}
                            blocked.extend(dependents[dependent])
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    for dependent in dependents[task_id]:
                        if dependent in results:
                            continue
                        remaining[dependent] -= 1
                        if remaining[dependent] == 0:
                            ready.append(dependent)

    return {task_id: results[task_id] for task_id in task_ids}
