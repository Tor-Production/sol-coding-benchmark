"""An in-memory, optimistic serializable key/value store."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's reads or writes."""


def _key(value):
    if not isinstance(value, str) or not value:
        raise ValueError("key must be a nonempty string")


def _value(value):
    if type(value) is not int:
        raise ValueError("value must be an integer")


def _version(value):
    if type(value) is not int or value < 0:
        raise ValueError("version must be a nonnegative integer")


def _data(value):
    if not isinstance(value, dict):
        raise ValueError("data must be a dict")
    for key, item in value.items():
        _key(key)
        _value(item)
    return dict(value)


def _copy_record(record):
    return {"version": record["version"],
            "writes": [[key, value] for key, value in record["writes"]]}


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _data(initial)
        self._version = 0
        self._base_version = 0
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        _version(version)
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside retained history")
        return [_copy_record(record) for record in self._records
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        _version(base)
        data = _data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        retained = []
        for expected, record in enumerate(records, base + 1):
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            if type(record["version"]) is not int or record["version"] != expected:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied_writes = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a pair")
                key, value = pair
                _key(key)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    _value(value)
                copied_writes.append([key, value])
                previous = key
            retained.append({"version": expected, "writes": copied_writes})

        # Replay only after the complete log has passed validation.
        for record in retained:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls()
        engine._data = data
        engine._version = base + len(retained)
        engine._base_version = base
        engine._records = retained
        return engine


class _Transaction:
    def __init__(self, engine, version, snapshot):
        self._engine = engine
        self._version = version
        self._snapshot = snapshot
        self._writes = {}
        self._reads = set()
        self._prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._ensure_open()
        _key(key)
        self._reads.add(key)
        return self._writes.get(key, self._snapshot.get(key))

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._prefixes.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._ensure_open()
        _key(key)
        _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        _key(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint name")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _key(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._ensure_open()
        self._closed = True
        engine = self._engine
        for record in engine._records:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if (key in self._reads or key in self._writes or
                        any(key.startswith(prefix) for prefix in self._prefixes)):
                    raise Conflict("concurrent committed write conflicts")

        if self._writes:
            engine._version += 1
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
            engine._records.append({"version": engine._version, "writes": writes})
        return engine._version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()
