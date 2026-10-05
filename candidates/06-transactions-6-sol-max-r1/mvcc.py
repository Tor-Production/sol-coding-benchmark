"""An in-memory MVCC engine with optimistic serializable validation."""


class Conflict(RuntimeError):
    """A transaction observed a key or range changed since its snapshot."""


def _valid_key(key):
    return isinstance(key, str) and bool(key)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(version):
    return _valid_value(version) and version >= 0


def _copy_record(record):
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        if not isinstance(initial, dict):
            raise ValueError("initial data must be a dict")
        for key, value in initial.items():
            if not _valid_key(key) or not _valid_value(value):
                raise ValueError("invalid initial key or value")

        self._data = initial.copy()
        self._version = 0
        self._base_version = 0
        self._history = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        return {"version": self._version, "data": dict(sorted(self._data.items()))}

    def log_since(self, version):
        if not _valid_version(version) or not self._base_version <= version <= self._version:
            raise ValueError("version is outside retained history")
        offset = version - self._base_version
        return [_copy_record(record) for record in self._history[offset:]]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base_version = checkpoint["version"]
        data = checkpoint["data"]
        if not _valid_version(base_version) or not isinstance(data, dict):
            raise ValueError("invalid checkpoint")
        for key, value in data.items():
            if not _valid_key(key) or not _valid_value(value):
                raise ValueError("invalid checkpoint data")
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Build local copies throughout validation, so a bad later record cannot
        # result in a partially restored engine or mutate either input.
        restored_data = data.copy()
        history = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            version = record["version"]
            writes = record["writes"]
            if (not _valid_version(version) or version != expected_version
                    or not isinstance(writes, list) or not writes):
                raise ValueError("invalid record version or writes")

            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid write pair")
                key, value = pair
                if (not _valid_key(key)
                        or (value is not None and not _valid_value(value))
                        or (previous_key is not None and key <= previous_key)):
                    raise ValueError("invalid write")
                copied_writes.append([key, value])
                previous_key = key

            for key, value in copied_writes:
                if value is None:
                    restored_data.pop(key, None)
                else:
                    restored_data[key] = value
            history.append({"version": version, "writes": copied_writes})
            expected_version += 1

        engine = cls(restored_data)
        engine._version = expected_version - 1
        engine._base_version = base_version
        engine._history = history
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

    def _require_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def _close(self):
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._read_keys.clear()
        self._read_prefixes.clear()
        self._savepoints.clear()

    def get(self, key):
        self._require_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._read_keys.add(key)
        return self._writes.get(key, self._snapshot.get(key))

    def scan(self, prefix=""):
        self._require_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._read_prefixes.add(prefix)
        visible = {key: value for key, value in self._snapshot.items()
                   if key.startswith(prefix)}
        for key, value in self._writes.items():
            if key.startswith(prefix):
                if value is None:
                    visible.pop(key, None)
                else:
                    visible[key] = value
        return dict(sorted(visible.items()))

    def put(self, key, value):
        self._require_open()
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("invalid key or value")
        self._writes[key] = value

    def delete(self, key):
        self._require_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._writes[key] = None

    def savepoint(self, name):
        self._require_open()
        if not _valid_key(name) or any(existing == name for existing, _ in self._savepoints):
            raise ValueError("invalid or duplicate savepoint name")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        for index, (existing, _) in enumerate(self._savepoints):
            if existing == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._require_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        self._require_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._require_open()
        engine = self._engine
        offset = self._snapshot_version - engine._base_version
        for record in engine._history[offset:]:
            for key, _ in record["writes"]:
                if (key in self._read_keys or key in self._writes
                        or any(key.startswith(prefix) for prefix in self._read_prefixes)):
                    self._close()
                    raise Conflict("committed write conflicts with transaction")

        if self._writes:
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            new_data = engine._data.copy()
            for key, value in writes:
                if value is None:
                    new_data.pop(key, None)
                else:
                    new_data[key] = value
            version = engine._version + 1
            engine._data = new_data
            engine._version = version
            engine._history.append({"version": version, "writes": writes})

        result = engine._version
        self._close()
        return result

    def abort(self):
        self._require_open()
        self._close()
