from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate a DAG, then execute it with bounded concurrent workers."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Snapshot inputs so neither validation nor scheduling edits the caller's data.
    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        if not isinstance(task["deps"], list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        deps = tuple(task["deps"])
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep == task_id or dep in seen:
                raise ValueError("self-dependencies and duplicate dependencies are invalid")
            seen.add(dep)
        dependencies[task_id] = deps
        functions[task_id] = task["fn"]

    children = {task_id: [] for task_id in dependencies}
    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in dependencies:
                raise ValueError("unknown dependency: " + dep)
            children[dep].append(task_id)

    # Kahn's algorithm checks the entire graph without recursive traversal.
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    roots = sorted(task_id for task_id, count in remaining.items() if count == 0)
    to_check = deque(roots)
    counts = remaining.copy()
    checked = 0
    while to_check:
        task_id = to_check.popleft()
        checked += 1
        for child in children[task_id]:
            counts[child] -= 1
            if counts[child] == 0:
                to_check.append(child)
    if checked != len(dependencies):
        raise ValueError("dependency graph contains a cycle")
    if not dependencies:
        return {}

    ready = deque(roots)
    results = {}
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                if task_id not in results:
                    running[executor.submit(functions[task_id])] = task_id
            if not running:
                break
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    # A failed ancestor makes a task impossible to start, even
                    # if other parents are still running. Visit each skip once.
                    blocked = list(children[task_id])
                    while blocked:
                        child = blocked.pop()
                        if child not in results:
                            results[child] = {"status": "skipped"}
                            blocked.extend(children[child])
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    for child in children[task_id]:
                        if child not in results:
                            remaining[child] -= 1
                            if remaining[child] == 0:
                                ready.append(child)

    return {task_id: results[task_id] for task_id in sorted(dependencies)}
