"""A small, bounded, concurrent DAG executor."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Validate and execute *tasks*, returning one result for every task."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    # Snapshot all execution-relevant input while validating it.  Besides
    # avoiding mutation of the caller's objects, this ensures no function can
    # run until the whole input has been checked.
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
            raise ValueError("deps must be a list")
        if not callable(fn):
            raise ValueError("fn must be callable")

        copied_deps = []
        seen = set()
        for dependency in deps:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            if dependency == task_id:
                raise ValueError("task cannot depend on itself")
            seen.add(dependency)
            copied_deps.append(dependency)
        dependencies[task_id] = copied_deps
        functions[task_id] = fn

    for deps in dependencies.values():
        if any(dependency not in dependencies for dependency in deps):
            raise ValueError("unknown dependency")

    dependents = {task_id: [] for task_id in dependencies}
    indegree = {}
    for task_id, deps in dependencies.items():
        indegree[task_id] = len(deps)
        for dependency in deps:
            dependents[dependency].append(task_id)

    # Iterative cycle detection handles very deep graphs without recursion.
    validation_ready = [task_id for task_id, degree in indegree.items()
                        if degree == 0]
    heapq.heapify(validation_ready)
    validation_degrees = indegree.copy()
    visited = 0
    while validation_ready:
        task_id = heapq.heappop(validation_ready)
        visited += 1
        for child in dependents[task_id]:
            validation_degrees[child] -= 1
            if validation_degrees[child] == 0:
                heapq.heappush(validation_ready, child)
    if visited != len(dependencies):
        raise ValueError("dependency graph contains a cycle")

    if not dependencies:
        return {}

    remaining = indegree.copy()
    blocked = {task_id: False for task_id in dependencies}
    ready = [task_id for task_id, degree in remaining.items() if degree == 0]
    heapq.heapify(ready)
    results = {}

    def finish(task_id, successful):
        """Propagate a terminal state, iteratively including skipped nodes."""
        queue = [(task_id, successful)]
        while queue:
            parent, parent_succeeded = queue.pop()
            for child in dependents[parent]:
                remaining[child] -= 1
                if not parent_succeeded:
                    blocked[child] = True
                if remaining[child] == 0:
                    if blocked[child]:
                        results[child] = {"status": "skipped"}
                        queue.append((child, False))
                    else:
                        heapq.heappush(ready, child)

    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                running[executor.submit(functions[task_id])] = task_id

            if not running:
                continue
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    finish(task_id, False)
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    finish(task_id, True)

    return {task_id: results[task_id] for task_id in sorted(results)}
