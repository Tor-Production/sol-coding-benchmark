"""A small, bounded-concurrency dependency graph executor."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Validate and execute a directed acyclic graph of zero-argument tasks.

    Validation is deliberately completed before the executor is created, so a
    bad graph can never cause even an otherwise-valid function to be called.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    # Keep private snapshots of all execution-relevant input.  Besides making
    # scheduling straightforward, this ensures dependency lists are untouched.
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

        copied_deps = []
        seen_deps = set()
        for dependency_id in deps:
            if not isinstance(dependency_id, str) or not dependency_id:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency_id == task_id:
                raise ValueError("tasks may not depend on themselves")
            if dependency_id in seen_deps:
                raise ValueError("duplicate dependency")
            seen_deps.add(dependency_id)
            copied_deps.append(dependency_id)

        dependencies[task_id] = tuple(copied_deps)
        functions[task_id] = fn

    task_ids = set(dependencies)
    dependents = {task_id: [] for task_id in dependencies}
    indegree = {}
    for task_id, deps in dependencies.items():
        indegree[task_id] = len(deps)
        for dependency_id in deps:
            if dependency_id not in task_ids:
                raise ValueError("unknown dependency")
            dependents[dependency_id].append(task_id)

    # Kahn's algorithm validates cycles without recursion, including for very
    # deep graphs.  Use a separate degree table needed only for validation.
    validation_degree = indegree.copy()
    validation_ready = [
        task_id for task_id, degree in validation_degree.items() if degree == 0
    ]
    heapq.heapify(validation_ready)
    visited = 0
    while validation_ready:
        task_id = heapq.heappop(validation_ready)
        visited += 1
        for child_id in dependents[task_id]:
            validation_degree[child_id] -= 1
            if validation_degree[child_id] == 0:
                heapq.heappush(validation_ready, child_id)
    if visited != len(dependencies):
        raise ValueError("dependency graph contains a cycle")

    if not dependencies:
        return {}

    # A task is pending until it is submitted, completed, or proven to be a
    # descendant of a failure.  All scheduler state is owned by this thread.
    pending = set(dependencies)
    remaining = indegree.copy()
    ready = [task_id for task_id, degree in remaining.items() if degree == 0]
    heapq.heapify(ready)
    results = {}

    def skip_descendants(task_id):
        """Iteratively mark all still-pending descendants as skipped."""
        stack = list(dependents[task_id])
        while stack:
            child_id = stack.pop()
            if child_id not in pending:
                continue
            pending.remove(child_id)
            results[child_id] = {"status": "skipped"}
            stack.extend(dependents[child_id])

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while pending or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                # A failure can leave a formerly-ready ID in the heap while
                # skip propagation has already removed it from pending.
                if task_id not in pending:
                    continue
                pending.remove(task_id)
                future = executor.submit(functions[task_id])
                running[future] = task_id

            if not running:
                # A validated DAG always has progress available.  This guard
                # keeps an internal invariant failure from becoming a spin.
                raise RuntimeError("graph scheduler made no progress")

            completed, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in completed:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exception:
                    results[task_id] = {
                        "status": "failed",
                        "error": str(exception),
                    }
                    skip_descendants(task_id)
                    continue

                results[task_id] = {"status": "completed", "value": value}
                for child_id in dependents[task_id]:
                    if child_id not in pending:
                        continue
                    remaining[child_id] -= 1
                    if remaining[child_id] == 0:
                        heapq.heappush(ready, child_id)

    return {task_id: results[task_id] for task_id in sorted(results)}
