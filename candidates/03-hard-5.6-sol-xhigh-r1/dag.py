"""A small, dependency-aware thread executor."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def _validated_graph(tasks, max_workers):
    """Return an immutable-enough snapshot after validating the whole graph."""
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers <= 0
    ):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")

    known_ids = set(task_ids)
    graph = {}
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
                raise ValueError("task dependencies must not contain duplicates")
            if dependency == task_id:
                raise ValueError("tasks cannot depend on themselves")
            if dependency not in known_ids:
                raise ValueError("task has an unknown dependency")
            seen.add(dependency)
            copied_deps.append(dependency)
        graph[task_id] = (tuple(copied_deps), fn)

    # Kahn's algorithm validates acyclicity without depending on recursion depth.
    remaining = {task_id: len(deps) for task_id, (deps, _) in graph.items()}
    dependents = {task_id: [] for task_id in graph}
    for task_id, (deps, _) in graph.items():
        for dependency in deps:
            dependents[dependency].append(task_id)

    roots = deque(task_id for task_id, count in remaining.items() if count == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for dependent in dependents[task_id]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                roots.append(dependent)
    if visited != len(graph):
        raise ValueError("task graph contains a cycle")

    return graph, dependents


def _call(fn):
    """Turn an ordinary callable exception into a scheduler outcome."""
    try:
        return True, fn()
    except Exception as exc:
        return False, str(exc)


def run_graph(tasks, max_workers=2):
    """Validate and execute a DAG using at most ``max_workers`` threads."""
    graph, dependents = _validated_graph(tasks, max_workers)
    if not graph:
        return {}

    unfinished_dependencies = {
        task_id: len(deps) for task_id, (deps, _) in graph.items()
    }
    has_failed_dependency = {task_id: False for task_id in graph}
    ready = [task_id for task_id, count in unfinished_dependencies.items() if count == 0]
    heapq.heapify(ready)
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                future = executor.submit(_call, graph[task_id][1])
                running[future] = task_id

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            terminal = deque()
            for future in sorted(done, key=lambda item: running[item]):
                task_id = running.pop(future)
                succeeded, payload = future.result()
                if succeeded:
                    results[task_id] = {"status": "completed", "value": payload}
                else:
                    results[task_id] = {"status": "failed", "error": payload}
                terminal.append(task_id)

            # A skipped task is itself an unsuccessful dependency. Iteratively
            # propagate that fact so very deep failure chains remain safe.
            while terminal:
                task_id = terminal.popleft()
                succeeded = results[task_id]["status"] == "completed"
                for dependent in dependents[task_id]:
                    unfinished_dependencies[dependent] -= 1
                    if not succeeded:
                        has_failed_dependency[dependent] = True
                    if unfinished_dependencies[dependent] == 0:
                        if has_failed_dependency[dependent]:
                            results[dependent] = {"status": "skipped"}
                            terminal.append(dependent)
                        else:
                            heapq.heappush(ready, dependent)

    return {task_id: results[task_id] for task_id in sorted(graph)}
