"""Concurrent execution for validated dependency graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Execute *tasks* once their dependencies have completed successfully.

    Validation and execution are deliberately separate: no executor is created
    until a complete, acyclic snapshot of the graph has been built.
    """
    if (
        not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or max_workers <= 0
    ):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    dependencies = {}
    functions = {}

    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")

        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list):
            raise ValueError("task deps must be a list")
        if not callable(fn):
            raise ValueError("task fn must be callable")

        # Snapshot the caller's data so neither this function nor functions
        # executing later need to touch the supplied dependency lists.
        copied_deps = tuple(deps)
        if any(not isinstance(dep, str) or not dep for dep in copied_deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(set(copied_deps)) != len(copied_deps):
            raise ValueError("duplicate dependencies are not allowed")

        dependencies[task_id] = copied_deps
        functions[task_id] = fn

    task_ids = set(dependencies)
    dependents = {task_id: [] for task_id in dependencies}
    indegree = {}

    for task_id, deps in dependencies.items():
        if task_id in deps:
            raise ValueError("a task cannot depend on itself")
        if any(dep not in task_ids for dep in deps):
            raise ValueError("dependency refers to an unknown task")
        indegree[task_id] = len(deps)
        for dep in deps:
            dependents[dep].append(task_id)

    # Kahn's algorithm validates cycles without recursion, including for very
    # deep graphs. Work on a copy because execution needs fresh dependency
    # counts below.
    validation_counts = indegree.copy()
    validation_ready = deque(
        task_id for task_id, count in validation_counts.items() if count == 0
    )
    validated_count = 0
    while validation_ready:
        task_id = validation_ready.popleft()
        validated_count += 1
        for child in dependents[task_id]:
            validation_counts[child] -= 1
            if validation_counts[child] == 0:
                validation_ready.append(child)
    if validated_count != len(dependencies):
        raise ValueError("task graph contains a cycle")

    if not dependencies:
        return {}

    remaining = indegree.copy()
    has_unsuccessful_dependency = {task_id: False for task_id in dependencies}
    # The first tuple item gives newly unblocked dependents priority over roots
    # that were already waiting. This ensures a freed worker does not strand a
    # dependent behind an unrelated backlog of root tasks.
    ready = [
        (1, task_id) for task_id, count in remaining.items() if count == 0
    ]
    heapq.heapify(ready)
    results = {}

    def release_dependents(task_id, succeeded):
        """Release children iteratively, propagating skipped states."""
        terminal = deque([(task_id, succeeded)])
        while terminal:
            finished_id, finished_successfully = terminal.popleft()
            for child in dependents[finished_id]:
                remaining[child] -= 1
                if not finished_successfully:
                    has_unsuccessful_dependency[child] = True
                if remaining[child] != 0:
                    continue
                if has_unsuccessful_dependency[child]:
                    results[child] = {"status": "skipped"}
                    terminal.append((child, False))
                else:
                    heapq.heappush(ready, (0, child))

    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            # Submit only up to current capacity. This avoids burying a task
            # that becomes ready later behind a large executor work queue.
            while ready and len(running) < max_workers:
                _, task_id = heapq.heappop(ready)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            if not running:
                break

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: running[item]):
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {
                        "status": "failed",
                        "error": str(exc),
                    }
                    succeeded = False
                else:
                    results[task_id] = {
                        "status": "completed",
                        "value": value,
                    }
                    succeeded = True
                release_dependents(task_id, succeeded)

    return {task_id: results[task_id] for task_id in sorted(dependencies)}
