"""Execute dependency graphs using a bounded pool of threads."""

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Validate and execute tasks after all of their dependencies succeed."""
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Copy the graph so execution never needs to modify the caller's data.
    specs = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must have deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep == task_id or dep in seen:
                raise ValueError("self-dependencies and duplicate dependencies are invalid")
            seen.add(dep)
        specs[task_id] = (tuple(deps), task["fn"])

    dependents = {task_id: [] for task_id in specs}
    indegree = {}
    for task_id, (deps, _) in specs.items():
        indegree[task_id] = len(deps)
        for dep in deps:
            if dep not in specs:
                raise ValueError("unknown dependency")
            dependents[dep].append(task_id)

    # Kahn's algorithm validates cycles without recursion, even for long chains.
    remaining = indegree.copy()
    queue = deque(task_id for task_id, count in remaining.items() if count == 0)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in dependents[task_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    if visited != len(specs):
        raise ValueError("dependency graph contains a cycle")

    if not specs:
        return {}

    remaining = indegree.copy()
    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapify(ready)
    outcomes = {}
    running = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heappop(ready)
                running[executor.submit(specs[task_id][1])] = task_id

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    outcomes[task_id] = {"status": "failed", "error": str(exc)}
                    blocked = deque([task_id])
                    while blocked:
                        for child in dependents[blocked.popleft()]:
                            if child not in outcomes:
                                outcomes[child] = {"status": "skipped"}
                                blocked.append(child)
                else:
                    outcomes[task_id] = {"status": "completed", "value": value}
                    for child in dependents[task_id]:
                        if child not in outcomes:
                            remaining[child] -= 1
                            if remaining[child] == 0:
                                heappush(ready, child)

    return {task_id: outcomes[task_id] for task_id in sorted(specs)}
