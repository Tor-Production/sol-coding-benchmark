from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heappop, heappush


def run_graph(tasks, max_workers=2):
    """Validate a DAG, then run ready tasks with bounded concurrent workers."""
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers <= 0
    ):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")
    if any(not isinstance(task_id, str) or not task_id for task_id in tasks):
        raise ValueError("task IDs must be nonempty strings")

    task_ids = sorted(tasks)
    children = {task_id: [] for task_id in task_ids}
    remaining = {}
    functions = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary with deps and fn")
        if not isinstance(task["deps"], list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")

        deps = tuple(task["deps"])
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep == task_id:
                raise ValueError("a task cannot depend on itself")
            if dep not in children:
                raise ValueError("unknown dependency")
            seen.add(dep)
            children[dep].append(task_id)
        remaining[task_id] = len(deps)
        functions[task_id] = task["fn"]

    ready = [task_id for task_id in task_ids if remaining[task_id] == 0]
    # Kahn's algorithm validates the entire graph without recursive traversal.
    unchecked = remaining.copy()
    validation_queue = deque(ready)
    visited = 0
    while validation_queue:
        task_id = validation_queue.popleft()
        visited += 1
        for child in children[task_id]:
            unchecked[child] -= 1
            if unchecked[child] == 0:
                validation_queue.append(child)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")
    if not task_ids:
        return {}

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                running[executor.submit(functions[task_id])] = task_id

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    # A blocked task stays blocked even if its other parents succeed.
                    blocked = deque(children[task_id])
                    while blocked:
                        child = blocked.popleft()
                        if child in results:
                            continue
                        results[child] = {"status": "skipped"}
                        blocked.extend(children[child])
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    for child in children[task_id]:
                        if child in results:
                            continue
                        remaining[child] -= 1
                        if remaining[child] == 0:
                            heappush(ready, child)

    return {task_id: results[task_id] for task_id in task_ids}
