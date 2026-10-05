"""An in-memory MVCC store with serializable optimistic transactions."""


class Conflict(RuntimeError):
    """A concurrent commit invalidated a transaction's reads or writes."""


def _valid_key(key):
    return isinstance(key, str) and bool(key)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(version):
    return _valid_value(version) and version >= 0


def _copy_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    for key, value in data.items():
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("data must contain nonempty string keys and integer values")
    return dict(data)


def _copy_record(record):
    return {
        "version": record["version"],
        "writes": [pair.copy() for pair in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _copy_data(initial)
        self._version = 0
        self._base_version = 0
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if (not _valid_version(version)
                or version < self._base_version
                or version > self._version):
            raise ValueError("version is outside the retained log")
        start = version - self._base_version
        return [_copy_record(record) for record in self._records[start:]]

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) != {"version", "data"}):
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        if not _valid_version(base_version):
            raise ValueError("checkpoint version must be a nonnegative integer")
        data = _copy_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        validated_records = []
        next_version = base_version + 1
        for record in records:
            if (not isinstance(record, dict)
                    or set(record) != {"version", "writes"}
                    or not _valid_version(record["version"])
                    or record["version"] != next_version):
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a two-element list")
                key, value = pair
                if (not _valid_key(key)
                        or (value is not None and not _valid_value(value))
                        or (previous_key is not None and key <= previous_key)):
                    raise ValueError("writes must have sorted unique keys and valid values")
                copied_writes.append([key, value])
                previous_key = key

            for key, value in copied_writes:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            validated_records.append({"version": next_version, "writes": copied_writes})
            next_version += 1

        engine = cls(data)
        engine._base_version = base_version
        engine._version = next_version - 1
        engine._records = validated_records
        return engine


class _Transaction:
    def __init__(self, engine, version, snapshot):
        self._engine = engine
        self._version = version
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
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._require_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._read_prefixes.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible) if key.startswith(prefix)}

    def put(self, key, value):
        self._require_open()
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("put requires a nonempty string key and an integer value")
        self._writes[key] = value

    def delete(self, key):
        self._require_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._writes[key] = None

    def savepoint(self, name):
        self._require_open()
        if not _valid_key(name) or any(saved == name for saved, _ in self._savepoints):
            raise ValueError("savepoint name must be a unique nonempty string")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        for index, (saved, _) in enumerate(self._savepoints):
            if saved == name:
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
        try:
            start = self._version - engine._base_version
            for record in engine._records[start:]:
                for key, _ in record["writes"]:
                    if (key in self._read_keys
                            or key in self._writes
                            or any(key.startswith(prefix) for prefix in self._read_prefixes)):
                        raise Conflict("a committed write conflicts with this transaction")

            if self._writes:
                writes = [[key, self._writes[key]] for key in sorted(self._writes)]
                data = engine._data.copy()
                for key, value in writes:
                    if value is None:
                        data.pop(key, None)
                    else:
                        data[key] = value
                next_version = engine._version + 1
                engine._data = data
                engine._records.append({"version": next_version, "writes": writes})
                engine._version = next_version
            return engine._version
        finally:
            self._close()

    def abort(self):
        self._require_open()
        self._close()
