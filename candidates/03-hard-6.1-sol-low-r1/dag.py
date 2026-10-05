from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate the whole DAG, then execute it with bounded thread workers."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Snapshot dependencies without changing the caller's dictionaries or lists.
    graph = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        if not isinstance(task["deps"], list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        deps = tuple(task["deps"])
        if any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(set(deps)) != len(deps):
            raise ValueError("duplicate dependencies")
        graph[task_id] = (deps, task["fn"])

    children = {task_id: [] for task_id in graph}
    remaining = {}
    for task_id, (deps, _) in graph.items():
        remaining[task_id] = len(deps)
        for dep in deps:
            if dep == task_id or dep not in graph:
                raise ValueError("self or unknown dependency")
            children[dep].append(task_id)

    # Kahn's algorithm validates cycles without recursive traversal.
    counts = remaining.copy()
    roots = sorted(task_id for task_id in graph if counts[task_id] == 0)
    pending = deque(roots)
    visited = 0
    while pending:
        task_id = pending.popleft()
        visited += 1
        for child in children[task_id]:
            counts[child] -= 1
            if counts[child] == 0:
                pending.append(child)
    if visited != len(graph):
        raise ValueError("dependency cycle")
    if not graph:
        return {}

    results = {}
    ready = deque(roots)
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                if task_id not in results:
                    running[executor.submit(graph[task_id][1])] = task_id
            if not running:
                break
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    descendants = deque(children[task_id])
                    while descendants:
                        child = descendants.popleft()
                        if child not in results:
                            results[child] = {"status": "skipped"}
                            descendants.extend(children[child])
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    for child in children[task_id]:
                        remaining[child] -= 1
                        if remaining[child] == 0 and child not in results:
                            ready.append(child)

    return {task_id: results[task_id] for task_id in sorted(graph)}
