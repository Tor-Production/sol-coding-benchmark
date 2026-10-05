from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from heapq import heapify, heappop, heappush


def run_graph(tasks, max_workers=2):
    """Validate and execute a dependency graph with bounded concurrency."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, dict):
        raise ValueError("tasks must be a dictionary")

    task_ids = set(tasks)
    for task_id in task_ids:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    children = {task_id: [] for task_id in task_ids}
    for task_id, task in tasks.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list) or not callable(fn):
            raise ValueError("invalid task definition")

        copied_deps = tuple(deps)
        if any(not isinstance(dep, str) or not dep for dep in copied_deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(set(copied_deps)) != len(copied_deps):
            raise ValueError("duplicate dependency")
        if task_id in copied_deps:
            raise ValueError("a task cannot depend on itself")
        if any(dep not in task_ids for dep in copied_deps):
            raise ValueError("unknown dependency")

        dependencies[task_id] = copied_deps
        functions[task_id] = fn
        for dep in copied_deps:
            children[dep].append(task_id)

    # Kahn's algorithm validates cycles without recursion (including long chains).
    indegree = {task_id: len(deps) for task_id, deps in dependencies.items()}
    roots = [task_id for task_id, degree in indegree.items() if degree == 0]
    heapify(roots)
    visited = 0
    while roots:
        task_id = heappop(roots)
        visited += 1
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heappush(roots, child)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    if not tasks:
        return {}

    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    blocked = {task_id: False for task_id in task_ids}
    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapify(ready)
    results = {}

    def finish(task_id, succeeded):
        """Propagate one terminal result, iteratively skipping blocked nodes."""
        pending = [(task_id, succeeded)]
        while pending:
            parent, parent_succeeded = pending.pop()
            for child in children[parent]:
                remaining[child] -= 1
                if not parent_succeeded:
                    blocked[child] = True
                if remaining[child] == 0:
                    if blocked[child]:
                        results[child] = {"status": "skipped"}
                        pending.append((child, False))
                    else:
                        heappush(ready, child)

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
                    finish(task_id, False)
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    finish(task_id, True)

    return {task_id: results[task_id] for task_id in sorted(task_ids)}
