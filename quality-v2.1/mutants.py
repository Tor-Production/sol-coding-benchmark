"""Same seeded reference-code defects for every candidate's added tests."""
MUTANTS = {
    '01-easy': [
        ('adjacency', 'intervals.py', 'lo <= result[-1][1] + 1', 'lo <= result[-1][1]'),
        ('bool_endpoint', 'intervals.py', 'type(x) is not int', 'not isinstance(x, int)'),
        ('input_mutation', 'intervals.py', '    work = []', '    if isinstance(ranges, list): ranges.sort()\n    work = []'),
        ('reverse_order', 'intervals.py', '    return result', '    return list(reversed(result))'),
    ],
    '02-medium': [
        ('expiry_boundary', 'cache.py', 'now >= expiry', 'now > expiry'),
        ('sliding_ttl', 'cache.py', '        self.data.move_to_end(key)\n        return self.data[key][0]',
         '        self.data.move_to_end(key)\n        self.data[key] = (self.data[key][0], self.clock() + self.ttl)\n        return self.data[key][0]'),
        ('fifo_reads', 'cache.py', '        self.data.move_to_end(key)\n        return self.data[key][0]', '        return self.data[key][0]'),
        ('expired_len', 'cache.py', '    def __len__(self):\n        self._purge()', '    def __len__(self):\n        pass'),
        ('missing_default', 'cache.py', '            return default', '            return None'),
    ],
    '03-hard': [
        ('failed_descendant_runs', 'dag.py', 'if any(results[dep]["status"] != "completed" for dep in tasks[key]["deps"]):', 'if False:'),
        ('serial_workers', 'dag.py', 'ThreadPoolExecutor(max_workers=max_workers)', 'ThreadPoolExecutor(max_workers=1)'),
        ('cycle_side_effect', 'dag.py', '        raise ValueError("Cycle")',
         '        next(t["fn"] for t in tasks.values() if not t["deps"])()\n        raise ValueError("Cycle")'),
        ('unsorted_results', 'dag.py', 'for key in sorted(tasks)', 'for key in reversed(sorted(tasks))'),
        ('lost_error', 'dag.py', '"error": str(exc)', '"error": ""'),
    ],
    '04-components': [
        ('retry_double_debit', 'reservation/store.py', '                return dict(row)',
         '                c.execute("UPDATE items SET available=available-? WHERE sku=?", (quantity, sku))\n                return dict(row)'),
        ('release_double_credit', 'reservation/store.py', 'if row["status"] == "active":', 'if True:'),
        ('oversell', 'reservation/store.py', 'if item[0] < quantity:', 'if False:'),
        ('ignore_key_conflict', 'reservation/store.py', 'if row["sku"] != sku or row["quantity"] != quantity:', 'if False:'),
        ('report_released_as_active', 'reservation/store.py', ' FROM reservations WHERE status=\'active\'', ' FROM reservations'),
        ('unbounded_http', 'reservation/http_api.py', 'if length > 65536:', 'if length > 1048576:'),
    ],
}


def apply(folder, task, name):
    entry = next(m for m in MUTANTS[task] if m[0] == name)
    _, filename, before, after = entry
    path = folder / filename
    content = path.read_text(encoding='utf-8')
    if content.count(before) != 1:
        raise ValueError(f'Non-unique mutation: {task}/{name}')
    path.write_text(content.replace(before, after), encoding='utf-8')
