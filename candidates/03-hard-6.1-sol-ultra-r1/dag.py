from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate a DAG, then execute each eligible task in a worker thread.

    Failed tasks block all their descendants. Results are returned in sorted
    task-ID order, regardless of the order in which workers finish.
    """
    if (isinstance(max_workers, bool) or not isinstance(max_workers, int)
            or max_workers <= 0):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")
    task_ids.sort()

    functions = {}
    dependents = {task_id: [] for task_id in task_ids}
    remaining = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary containing deps and fn")
        deps, fn = task["deps"], task["fn"]
        if not isinstance(deps, list):
            raise ValueError("deps must be a list")
        if not callable(fn):
            raise ValueError("fn must be callable")

        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError("dependency IDs must be nonempty strings")
            if dep in seen:
                raise ValueError("duplicate dependency")
            if dep not in dependents:
                raise ValueError("unknown dependency")
            if dep == task_id:
                raise ValueError("a task cannot depend on itself")
            seen.add(dep)
            dependents[dep].append(task_id)
        functions[task_id] = fn
        remaining[task_id] = len(deps)

    # Kahn's algorithm validates the entire graph without recursion or running
    # any user functions, including disconnected components containing cycles.
    ready = deque(task_id for task_id in task_ids if remaining[task_id] == 0)
    unchecked = remaining.copy()
    to_check = ready.copy()
    checked = 0
    while to_check:
        task_id = to_check.popleft()
        checked += 1
        for child in dependents[task_id]:
            unchecked[child] -= 1
            if unchecked[child] == 0:
                to_check.append(child)
    if checked != len(task_ids):
        raise ValueError("the graph contains a cycle")
    if not task_ids:
        return {}

    results = {}
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            # Consume whichever workers finish first so a dependent can start
            # while an unrelated task is still running.
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    blocked = deque(dependents[task_id])
                    while blocked:
                        child = blocked.popleft()
                        if child not in results:
                            results[child] = {"status": "skipped"}
                            blocked.extend(dependents[child])
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    for child in dependents[task_id]:
                        remaining[child] -= 1
                        if remaining[child] == 0 and child not in results:
                            ready.append(child)

    return {task_id: results[task_id] for task_id in task_ids}
