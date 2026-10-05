from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def _validate_graph(tasks):
    """Snapshot the graph and reject malformed or cyclic input iteratively."""
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    for task_id in tasks:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    functions = {}
    children = {task_id: [] for task_id in tasks}
    remaining = {}
    for task_id, task in tasks.items():
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
            if dependency not in children:
                raise ValueError("unknown dependency")
            if dependency == task_id:
                raise ValueError("a task cannot depend on itself")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            seen.add(dependency)
            children[dependency].append(task_id)

        functions[task_id] = task["fn"]
        remaining[task_id] = len(deps)

    # Kahn's algorithm validates every component without recursive traversal.
    counts = remaining.copy()
    ready = deque(task_id for task_id, count in counts.items() if count == 0)
    visited = 0
    while ready:
        task_id = ready.popleft()
        visited += 1
        for child in children[task_id]:
            counts[child] -= 1
            if counts[child] == 0:
                ready.append(child)
    if visited != len(functions):
        raise ValueError("dependency graph contains a cycle")

    return functions, children, remaining


def run_graph(tasks, max_workers=2):
    """Execute a fully validated DAG with bounded concurrent workers."""
    if (not isinstance(max_workers, int) or isinstance(max_workers, bool)
            or max_workers <= 0):
        raise ValueError("max_workers must be a positive integer")
    functions, children, remaining = _validate_graph(tasks)
    if not functions:
        return {}

    ready = deque(sorted(task_id for task_id, count in remaining.items() if count == 0))
    results = {}
    blocked = set()

    def finish(task_id, outcome):
        # Propagate terminal results iteratively, including chains of skips.
        finished = deque([(task_id, outcome)])
        while finished:
            completed_id, result = finished.popleft()
            results[completed_id] = result
            for child in children[completed_id]:
                remaining[child] -= 1
                if result["status"] != "completed":
                    blocked.add(child)
                if remaining[child] == 0:
                    if child in blocked:
                        finished.append((child, {"status": "skipped"}))
                    else:
                        ready.append(child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as error:
                    finish(task_id, {"status": "failed", "error": str(error)})
                else:
                    finish(task_id, {"status": "completed", "value": value})

    return {task_id: results[task_id] for task_id in sorted(functions)}
