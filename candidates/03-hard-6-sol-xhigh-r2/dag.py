"""Execute dependency graphs with a bounded pool of threads."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate and execute *tasks*, returning results in task-ID order.

    A task becomes runnable only when every dependency has completed
    successfully. Failed tasks and their descendants do not stop independent
    branches of the graph.
    """
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    dependencies = {}
    functions = {}
    for task_id in tasks:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError(f"malformed task: {task_id}")
        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list) or not callable(fn):
            raise ValueError(f"malformed task: {task_id}")
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError(f"invalid dependency for task: {task_id}")
            if dep in seen:
                raise ValueError(f"duplicate dependency for task: {task_id}")
            if dep not in tasks:
                raise ValueError(f"unknown dependency for task: {task_id}")
            if dep == task_id:
                raise ValueError(f"self-dependency for task: {task_id}")
            seen.add(dep)
        dependencies[task_id] = tuple(deps)
        functions[task_id] = fn

    if not tasks:
        return {}

    dependents = {task_id: [] for task_id in tasks}
    for task_id in sorted(tasks):
        for dep in dependencies[task_id]:
            dependents[dep].append(task_id)

    # Kahn's algorithm checks every component without recursive traversal.
    remaining = {task_id: len(dependencies[task_id]) for task_id in tasks}
    roots = deque(task_id for task_id in sorted(tasks) if remaining[task_id] == 0)
    visited = 0
    while roots:
        task_id = roots.popleft()
        visited += 1
        for child in dependents[task_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                roots.append(child)
    if visited != len(tasks):
        raise ValueError("dependency graph contains a cycle")

    remaining = {task_id: len(dependencies[task_id]) for task_id in tasks}
    blocked = {task_id: False for task_id in tasks}
    ready = deque(task_id for task_id in sorted(tasks) if remaining[task_id] == 0)
    results = {}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                else:
                    results[task_id] = {"status": "completed", "value": value}

                # Propagate skips iteratively, including across multiple parents.
                settled = deque([task_id])
                while settled:
                    parent = settled.popleft()
                    parent_failed = results[parent]["status"] != "completed"
                    for child in dependents[parent]:
                        remaining[child] -= 1
                        if parent_failed:
                            blocked[child] = True
                        if remaining[child] == 0:
                            if blocked[child]:
                                results[child] = {"status": "skipped"}
                                settled.append(child)
                            else:
                                ready.append(child)

    return {task_id: results[task_id] for task_id in sorted(tasks)}
