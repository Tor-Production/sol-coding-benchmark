from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate the entire DAG, then execute it with bounded worker threads."""
    if (isinstance(max_workers, bool) or not isinstance(max_workers, int)
            or max_workers <= 0):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Snapshot inputs so validation and scheduling never modify caller data.
    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")
    task_ids.sort()
    dependencies = {}
    functions = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        if not isinstance(task["deps"], list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        deps = list(task["deps"])
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen or dep == task_id or dep not in tasks:
                raise ValueError("duplicate, self, or unknown dependency")
            seen.add(dep)
        dependencies[task_id] = deps
        functions[task_id] = task["fn"]

    children = {task_id: [] for task_id in task_ids}
    remaining = {task_id: len(dependencies[task_id]) for task_id in task_ids}
    for task_id, deps in dependencies.items():
        for dep in deps:
            children[dep].append(task_id)

    # Kahn's algorithm validates cycles without recursion, even for long chains.
    roots = [task_id for task_id in task_ids if remaining[task_id] == 0]
    validation_counts = remaining.copy()
    queue = deque(roots)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in children[task_id]:
            validation_counts[child] -= 1
            if validation_counts[child] == 0:
                queue.append(child)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")
    if not task_ids:
        return {}

    results = {}
    ready = deque(roots)
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
                    # A descendant cannot be running before this parent succeeds.
                    blocked = deque(children[task_id])
                    while blocked:
                        child = blocked.popleft()
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

    return {task_id: results[task_id] for task_id in task_ids}
