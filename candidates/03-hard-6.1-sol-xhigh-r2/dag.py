from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate the entire DAG, then execute it with bounded concurrency.

    Only the calling thread updates graph state. Workers execute task functions;
    completions release dependents, and failures iteratively block descendants.
    """
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers <= 0
    ):
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    task_specs = dict(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_specs):
        raise ValueError("task IDs must be nonempty strings")

    task_ids = sorted(task_specs)
    functions = {}
    dependents = {task_id: [] for task_id in task_ids}
    remaining = {}
    for task_id in task_ids:
        spec = task_specs[task_id]
        if not isinstance(spec, dict) or "deps" not in spec or "fn" not in spec:
            raise ValueError("each task must be a dictionary containing deps and fn")
        if not isinstance(spec["deps"], list):
            raise ValueError("deps must be a list of task IDs")
        if not callable(spec["fn"]):
            raise ValueError("fn must be callable")

        seen = set()
        for dependency in spec["deps"]:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError("dependency IDs must be nonempty strings")
            if dependency in seen:
                raise ValueError("duplicate dependency")
            if dependency not in task_specs:
                raise ValueError("unknown dependency")
            if dependency == task_id:
                raise ValueError("self-dependency")
            seen.add(dependency)
            dependents[dependency].append(task_id)

        functions[task_id] = spec["fn"]
        remaining[task_id] = len(seen)

    # Kahn's algorithm checks all components without recursion or task execution.
    ready = deque(task_id for task_id in task_ids if remaining[task_id] == 0)
    unchecked = remaining.copy()
    to_check = ready.copy()
    checked = 0
    while to_check:
        task_id = to_check.popleft()
        checked += 1
        for dependent in dependents[task_id]:
            unchecked[dependent] -= 1
            if unchecked[dependent] == 0:
                to_check.append(dependent)
    if checked != len(task_ids):
        raise ValueError("dependency graph contains a cycle")
    if not task_ids:
        return {}

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as error:
                    results[task_id] = {"status": "failed", "error": str(error)}
                    blocked = list(dependents[task_id])
                    while blocked:
                        dependent = blocked.pop()
                        if dependent in results:
                            continue
                        results[dependent] = {"status": "skipped"}
                        blocked.extend(dependents[dependent])
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    for dependent in dependents[task_id]:
                        if dependent in results:
                            continue
                        remaining[dependent] -= 1
                        if remaining[dependent] == 0:
                            ready.append(dependent)

    return {task_id: results[task_id] for task_id in task_ids}
