"""A small concurrent executor for dependency graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def _validated_graph(tasks, max_workers):
    """Return immutable task data and adjacency lists for a valid DAG."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    task_ids = set(tasks)
    for task_id in task_ids:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
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
            if dependency not in task_ids:
                raise ValueError("unknown dependency")
            seen.add(dependency)
            copied_deps.append(dependency)

        # Snapshot the graph before any user function has a chance to mutate it.
        dependencies[task_id] = tuple(copied_deps)
        functions[task_id] = fn

    dependents = {task_id: [] for task_id in task_ids}
    indegree = {}
    for task_id, deps in dependencies.items():
        indegree[task_id] = len(deps)
        for dependency in deps:
            dependents[dependency].append(task_id)

    # Kahn's algorithm validates deep graphs without using Python recursion.
    remaining = indegree.copy()
    roots = deque(task_id for task_id, count in remaining.items() if count == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for dependent in dependents[task_id]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                roots.append(dependent)
    if visited != len(task_ids):
        raise ValueError("task graph contains a cycle")

    return functions, dependents, indegree


def run_graph(tasks, max_workers=2):
    """Validate and execute *tasks* with at most *max_workers* threads."""
    functions, dependents, unresolved = _validated_graph(tasks, max_workers)
    if not functions:
        return {}

    # A heap makes selection among simultaneously-ready tasks deterministic.
    ready = [task_id for task_id, count in unresolved.items() if count == 0]
    heapq.heapify(ready)
    blocked = {task_id: False for task_id in functions}
    outcomes = {}
    running = {}

    def release_dependents(completed_id, succeeded):
        """Propagate one terminal result, iteratively cascading skipped tasks."""
        terminal = deque([(completed_id, succeeded)])
        while terminal:
            task_id, was_successful = terminal.popleft()
            for dependent in dependents[task_id]:
                unresolved[dependent] -= 1
                if not was_successful:
                    blocked[dependent] = True
                if unresolved[dependent] != 0:
                    continue
                if blocked[dependent]:
                    outcomes[dependent] = {"status": "skipped"}
                    terminal.append((dependent, False))
                else:
                    heapq.heappush(ready, dependent)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            if not running:
                # In a validated DAG this is reachable only after skip cascades.
                continue

            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(finished, key=running.__getitem__):
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    outcomes[task_id] = {
                        "status": "failed",
                        "error": str(exc),
                    }
                    release_dependents(task_id, False)
                else:
                    outcomes[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    release_dependents(task_id, True)

    return {task_id: outcomes[task_id] for task_id in sorted(functions)}
