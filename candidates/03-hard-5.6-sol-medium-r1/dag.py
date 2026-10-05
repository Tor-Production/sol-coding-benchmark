from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate and execute a dependency graph with bounded concurrency."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("max_workers must be a positive integer")
    if max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    # Copy the graph metadata so execution neither mutates nor depends on later
    # mutations of the caller's dependency lists.
    task_ids = list(tasks)
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("task IDs must be nonempty strings")

    dependencies = {}
    functions = {}
    for task_id in task_ids:
        task = tasks[task_id]
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must be a dictionary with deps and fn")

        deps = task["deps"]
        fn = task["fn"]
        if not isinstance(deps, list):
            raise ValueError("task deps must be a list")
        if not callable(fn):
            raise ValueError("task fn must be callable")
        if any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("dependency IDs must be nonempty strings")
        if len(deps) != len(set(deps)):
            raise ValueError("duplicate dependency")
        if task_id in deps:
            raise ValueError("task cannot depend on itself")

        dependencies[task_id] = tuple(deps)
        functions[task_id] = fn

    known_ids = set(task_ids)
    for deps in dependencies.values():
        if any(dep not in known_ids for dep in deps):
            raise ValueError("unknown dependency")

    dependents = {task_id: [] for task_id in task_ids}
    indegree = {task_id: len(dependencies[task_id]) for task_id in task_ids}
    for task_id, deps in dependencies.items():
        for dep in deps:
            dependents[dep].append(task_id)
    for children in dependents.values():
        children.sort()

    # Kahn's algorithm validates cycles without using Python recursion.
    validation_indegree = indegree.copy()
    validation_queue = deque(
        task_id for task_id in sorted(task_ids) if validation_indegree[task_id] == 0
    )
    visited = 0
    while validation_queue:
        task_id = validation_queue.popleft()
        visited += 1
        for child in dependents[task_id]:
            validation_indegree[child] -= 1
            if validation_indegree[child] == 0:
                validation_queue.append(child)
    if visited != len(task_ids):
        raise ValueError("dependency graph contains a cycle")

    if not task_ids:
        return {}

    remaining = indegree.copy()
    has_failed_dependency = {task_id: False for task_id in task_ids}
    ready = deque(task_id for task_id in sorted(task_ids) if remaining[task_id] == 0)
    results = {}

    def propagate_terminal(task_id, succeeded):
        """Propagate one terminal state and return newly runnable tasks."""
        newly_ready = []
        terminal = deque([(task_id, succeeded)])
        while terminal:
            finished_id, finished_successfully = terminal.popleft()
            for child in dependents[finished_id]:
                remaining[child] -= 1
                if not finished_successfully:
                    has_failed_dependency[child] = True
                if remaining[child] == 0:
                    if has_failed_dependency[child]:
                        results[child] = {"status": "skipped"}
                        terminal.append((child, False))
                    else:
                        newly_ready.append(child)
        return newly_ready

    running = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while ready or running:
            while ready and len(running) < max_workers:
                task_id = ready.popleft()
                running[executor.submit(functions[task_id])] = task_id

            if not running:
                break

            completed, _ = wait(running, return_when=FIRST_COMPLETED)
            newly_ready = []
            for future in sorted(completed, key=lambda item: running[item]):
                task_id = running.pop(future)
                try:
                    value = future.result()
                except Exception as exc:
                    results[task_id] = {"status": "failed", "error": str(exc)}
                    newly_ready.extend(propagate_terminal(task_id, False))
                else:
                    results[task_id] = {"status": "completed", "value": value}
                    newly_ready.extend(propagate_terminal(task_id, True))

            # Prefer tasks unlocked by this completion for the now-free slots.
            for task_id in reversed(sorted(newly_ready)):
                ready.appendleft(task_id)

    return {task_id: results[task_id] for task_id in sorted(task_ids)}
