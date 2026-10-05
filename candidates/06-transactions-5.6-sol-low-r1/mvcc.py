"""A small, in-memory serializable MVCC engine."""


class Conflict(RuntimeError):
    """Raised when optimistic validation cannot serialize a transaction."""


def _valid_key(value):
    return isinstance(value, str) and bool(value)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _copy_record(record):
    return {"version": record["version"],
            "writes": [[key, value] for key, value in record["writes"]]}


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        elif isinstance(initial, dict):
            if not all(_valid_key(k) and _valid_value(v)
                       for k, v in initial.items()):
                raise ValueError("initial data contains an invalid key or value")
            data = dict(initial)
        else:
            raise ValueError("initial must be a dict or None")
        self._data = data
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, dict(self._data))

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        if (not _valid_version(version) or version < self._base_version or
                version > self._version):
            raise ValueError("version is outside retained history")
        return [_copy_record(r) for r in self._log if r["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        # Validate and replay only into local objects.  Thus a malformed suffix
        # is rejected without publishing a partially restored engine.
        if (not isinstance(checkpoint, dict) or
                set(checkpoint) != {"version", "data"}):
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        source_data = checkpoint["data"]
        if (not _valid_version(base) or not isinstance(source_data, dict) or
                not all(_valid_key(k) and _valid_value(v)
                        for k, v in source_data.items())):
            raise ValueError("invalid checkpoint")
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        data = dict(source_data)
        copied_records = []
        expected = base + 1
        for record in records:
            if (not isinstance(record, dict) or
                    set(record) != {"version", "writes"} or
                    not _valid_version(record["version"]) or
                    record["version"] != expected or
                    isinstance(record["version"], bool)):
                raise ValueError("invalid log record")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("invalid writes")
            copied_writes = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid write entry")
                key, value = pair
                if (not _valid_key(key) or
                        (value is not None and not _valid_value(value)) or
                        (previous is not None and key <= previous)):
                    raise ValueError("invalid write entry")
                copied_writes.append([key, value])
                previous = key
            for key, value in copied_writes:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            copied_records.append({"version": expected,
                                   "writes": copied_writes})
            expected += 1

        engine = cls.__new__(cls)
        engine._data = data
        engine._version = expected - 1
        engine._base_version = base
        engine._log = copied_records
        return engine


class _Transaction:
    def __init__(self, engine, version, snapshot):
        self._engine = engine
        self._snapshot_version = version
        self._snapshot = snapshot
        self._writes = {}
        self._read_keys = set()
        self._read_prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._read_keys.add(key)
        return self._writes[key] if key in self._writes else self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._read_prefixes.add(prefix)
        visible = dict(self._snapshot)
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._ensure_open()
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("invalid key or value")
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        if any(saved == name for saved, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, dict(self._writes)))

    def _savepoint_index(self, name):
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        for index, (saved, _) in enumerate(self._savepoints):
            if saved == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = dict(self._savepoints[index][1])
        self._savepoints = self._savepoints[:index + 1]

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]

    def commit(self):
        self._ensure_open()
        self._closed = True
        watched = self._read_keys | set(self._writes)
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if (key in watched or
                        any(key.startswith(p) for p in self._read_prefixes)):
                    raise Conflict("transaction conflicts with committed history")
        if self._writes:
            version = self._engine._version + 1
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._version = version
            self._engine._log.append({"version": version, "writes": writes})
        return self._engine._version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._savepoints.clear()
        self._read_keys.clear()
        self._read_prefixes.clear()
