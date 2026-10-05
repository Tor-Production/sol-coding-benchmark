from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Validate a DAG, then execute ready tasks with bounded concurrency."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Copy the graph's structure so execution never changes the caller's data.
    tasks = dict(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in tasks):
        raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    children = {task_id: [] for task_id in tasks}
    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        if not isinstance(task["deps"], list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        deps = tuple(task["deps"])
        seen = set()
        for dependency in deps:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            if dependency not in tasks:
                raise ValueError("unknown dependency")
            if dependency == task_id:
                raise ValueError("self dependency")
            seen.add(dependency)
            children[dependency].append(task_id)
        dependencies[task_id] = deps
        functions[task_id] = task["fn"]

    # Kahn's algorithm validates even disconnected cycles, without recursion.
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    indegrees = remaining.copy()
    roots = [task_id for task_id, count in remaining.items() if count == 0]
    pending = deque(roots)
    visited = 0
    while pending:
        task_id = pending.popleft()
        visited += 1
        for child in children[task_id]:
            indegrees[child] -= 1
            if indegrees[child] == 0:
                pending.append(child)
    if visited != len(tasks):
        raise ValueError("dependency graph contains a cycle")
    if not tasks:
        return {}

    results = {}
    ready = roots
    heapify(ready)
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                if task_id not in results:
                    running[executor.submit(functions[task_id])] = task_id

            if not running:
                break
            completed, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in completed:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as error:
                    results[task_id] = {"status": "failed", "error": str(error)}
                    # Mark the entire blocked subtree, including shared children.
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
                                heappush(ready, child)

    return {task_id: results[task_id] for task_id in sorted(tasks)}
