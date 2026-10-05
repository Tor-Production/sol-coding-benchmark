"""Concurrent execution of validated dependency graphs."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def _validated_graph(tasks, max_workers):
    """Return private graph data, raising ValueError for invalid input."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    task_ids = set()
    for task_id in tasks:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        task_ids.add(task_id)

    dependencies = {}
    functions = {}
    dependents = {task_id: [] for task_id in task_ids}

    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list):
            raise ValueError("deps must be a list")
        if not callable(fn):
            raise ValueError("fn must be callable")

        seen = set()
        copied_deps = []
        for dependency in deps:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            seen.add(dependency)
            if dependency == task_id:
                raise ValueError("a task cannot depend on itself")
            if dependency not in task_ids:
                raise ValueError("unknown dependency")
            copied_deps.append(dependency)

        dependencies[task_id] = tuple(copied_deps)
        functions[task_id] = fn
        for dependency in copied_deps:
            dependents[dependency].append(task_id)

    # Validate acyclicity iteratively, both to validate the entire graph before
    # execution and to support graphs much deeper than Python's recursion limit.
    indegree = {task_id: len(deps) for task_id, deps in dependencies.items()}
    ready = [task_id for task_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        task_id = heapq.heappop(ready)
        visited += 1
        for dependent in dependents[task_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    return dependencies, functions, dependents


def run_graph(tasks, max_workers=2):
    """Execute a validated DAG with bounded concurrent workers."""
    dependencies, functions, dependents = _validated_graph(tasks, max_workers)
    if not dependencies:
        return {}

    remaining = {
        task_id: len(task_dependencies)
        for task_id, task_dependencies in dependencies.items()
    }
    blocked = {task_id: False for task_id in dependencies}
    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    results = {}

    def finish(task_id, succeeded):
        """Propagate one terminal task state without recursive calls."""
        newly_terminal = [(task_id, succeeded)]
        while newly_terminal:
            finished_id, finished_successfully = newly_terminal.pop()
            for dependent in dependents[finished_id]:
                remaining[dependent] -= 1
                if not finished_successfully:
                    blocked[dependent] = True
                if remaining[dependent] == 0:
                    if blocked[dependent]:
                        results[dependent] = {"status": "skipped"}
                        newly_terminal.append((dependent, False))
                    else:
                        heapq.heappush(ready, dependent)

    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                running[executor.submit(functions[task_id])] = task_id

            if not running:
                continue

            completed, _ = wait(running, return_when=FIRST_COMPLETED)
            # Stable processing makes which equally-ready task is submitted next
            # predictable without changing concurrency semantics.
            for future in sorted(completed, key=lambda item: running[item]):
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exception:
                    results[task_id] = {
                        "status": "failed",
                        "error": str(exception),
                    }
                    finish(task_id, False)
                else:
                    results[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    finish(task_id, True)

    return {task_id: results[task_id] for task_id in sorted(results)}
