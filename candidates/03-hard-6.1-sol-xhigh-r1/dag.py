from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def _validate_graph(tasks):
    """Snapshot the graph and reject invalid inputs before starting workers."""
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")

    functions = {}
    dependents = {task_id: [] for task_id in task_ids}
    remaining = {}

    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary containing deps and fn")
        if not isinstance(task["deps"], list):
            raise ValueError("deps must be a list")
        if not callable(task["fn"]):
            raise ValueError("fn must be callable")

        deps = tuple(task["deps"])
        seen = set()
        for dependency in deps:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            if dependency == task_id:
                raise ValueError("a task cannot depend on itself")
            if dependency not in dependents:
                raise ValueError("unknown dependency")
            seen.add(dependency)
            dependents[dependency].append(task_id)

        functions[task_id] = task["fn"]
        remaining[task_id] = len(deps)

    # Kahn's algorithm avoids recursion even for very deep dependency chains.
    counts = remaining.copy()
    ready = deque(task_id for task_id in task_ids if counts[task_id] == 0)
    visited = 0
    while ready:
        task_id = ready.popleft()
        visited += 1
        for dependent in dependents[task_id]:
            counts[dependent] -= 1
            if counts[dependent] == 0:
                ready.append(dependent)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    return functions, dependents, remaining


def run_graph(tasks, max_workers=2):
    """Execute a validated DAG with bounded concurrent workers."""
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers <= 0
    ):
        raise ValueError("max_workers must be a positive integer")

    functions, dependents, remaining = _validate_graph(tasks)
    task_ids = sorted(functions)
    if not task_ids:
        return {}

    ready = deque(task_id for task_id in task_ids if remaining[task_id] == 0)
    results = {}
    running = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as error:
                    results[task_id] = {"status": "failed", "error": str(error)}
                    # A failed prerequisite blocks descendants immediately,
                    # including joins whose other prerequisites are still running.
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
