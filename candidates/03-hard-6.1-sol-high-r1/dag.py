from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq


def run_graph(tasks, max_workers=2):
    """Validate a DAG, then execute its tasks with bounded concurrent threads."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Snapshot the inputs so neither validation nor execution modifies them.
    dependencies = {}
    functions = {}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list):
            raise ValueError("deps must be a list")
        if not callable(task["fn"]):
            raise ValueError("fn must be callable")
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep == task_id:
                raise ValueError("self dependency")
            seen.add(dep)
        dependencies[task_id] = tuple(deps)
        functions[task_id] = task["fn"]

    children = {task_id: [] for task_id in dependencies}
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in dependencies:
                raise ValueError("unknown dependency")
            children[dep].append(task_id)

    # Kahn's algorithm checks the entire graph without recursion or running fn.
    indegrees = remaining.copy()
    roots = [task_id for task_id, count in remaining.items() if count == 0]
    queue = deque(roots)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in children[task_id]:
            indegrees[child] -= 1
            if indegrees[child] == 0:
                queue.append(child)
    if visited != len(dependencies):
        raise ValueError("dependency cycle")
    if not dependencies:
        return {}

    ready = roots
    heapq.heapify(ready)
    results = {}
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = heapq.heappop(ready)
                running[executor.submit(functions[task_id])] = task_id

            # React to any completion, including one after a slow earlier task.
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    blocked = list(children[task_id])
                    while blocked:
                        child = blocked.pop()
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
                            heapq.heappush(ready, child)

    return {task_id: results[task_id] for task_id in sorted(dependencies)}
