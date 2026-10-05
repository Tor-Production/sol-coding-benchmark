from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def run_graph(tasks, max_workers=2):
    """Validate the entire DAG, then execute ready tasks on bounded threads."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if not isinstance(tasks, Mapping):
        raise ValueError("tasks must be a mapping")

    nodes = dict(tasks)
    if any(not isinstance(key, str) or not key for key in nodes):
        raise ValueError("task IDs must be nonempty strings")
    dependencies = {}
    functions = {}
    children = {key: [] for key in nodes}
    for key, task in nodes.items():
        if not isinstance(task, dict) or "deps" not in task or "fn" not in task:
            raise ValueError("each task must contain deps and fn")
        deps = task["deps"]
        if not isinstance(deps, list) or not callable(task["fn"]):
            raise ValueError("deps must be a list and fn must be callable")
        seen = set()
        for dep in deps:
            if not isinstance(dep, str) or not dep or dep not in nodes:
                raise ValueError("dependency must be a known task ID")
            if dep == key or dep in seen:
                raise ValueError("self-dependencies and duplicate dependencies are invalid")
            seen.add(dep)
            children[dep].append(key)
        dependencies[key] = tuple(deps)
        functions[key] = task["fn"]

    remaining = {key: len(deps) for key, deps in dependencies.items()}
    counts = remaining.copy()
    roots = [key for key in nodes if counts[key] == 0]
    queue = deque(roots)
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for child in children[key]:
            counts[child] -= 1
            if counts[child] == 0:
                queue.append(child)
    if visited != len(nodes):
        raise ValueError("graph contains a cycle")
    if not nodes:
        return {}

    results = {}
    blocked = set()
    ready = deque(roots)

    def finish(key, result):
        # Propagate terminal results iteratively, including skipped descendants.
        terminal = deque([(key, result)])
        while terminal:
            parent, outcome = terminal.popleft()
            results[parent] = outcome
            for child in children[parent]:
                if outcome["status"] != "completed":
                    blocked.add(child)
                remaining[child] -= 1
                if remaining[child] == 0:
                    if child in blocked:
                        terminal.append((child, {"status": "skipped"}))
                    else:
                        ready.append(child)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        running = {}
        while ready or running:
            while ready and len(running) < max_workers:
                key = ready.popleft()
                running[executor.submit(functions[key])] = key
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                key = running.pop(future)
                try:
                    outcome = {"status": "completed", "value": future.result()}
                except Exception as exc:
                    outcome = {"status": "failed", "error": str(exc)}
                finish(key, outcome)

    return {key: results[key] for key in sorted(nodes)}
