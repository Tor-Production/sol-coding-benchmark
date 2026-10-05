"""In-memory optimistic serializable store with checkpointable commit history."""


class Conflict(RuntimeError):
    pass


def _key(value):
    if not isinstance(value, str) or not value:
        raise ValueError("key and savepoint name must be nonempty strings")


def _value(value):
    if type(value) is not int:
        raise ValueError("stored value must be an integer")


def _data(value):
    if not isinstance(value, dict):
        raise ValueError("data must be a dict")
    for key, item in value.items():
        _key(key)
        _value(item)
    return dict(value)


def _record_copy(record):
    return {"version": record["version"],
            "writes": [[key, value] for key, value in record["writes"]]}


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _data(initial)
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        if (type(version) is not int or
                not self._base_version <= version <= self._version):
            raise ValueError("version outside retained history")
        return [_record_copy(record) for record in self._log
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        if type(base) is not int or base < 0:
            raise ValueError("invalid checkpoint version")
        data = _data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")
        log = []
        for expected, record in enumerate(records, base + 1):
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            if type(record["version"]) is not int or record["version"] != expected:
                raise ValueError("noncontiguous record version")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a pair")
                key, value = pair
                _key(key)
                if previous is not None and key <= previous:
                    raise ValueError("writes must have sorted unique keys")
                if value is not None:
                    _value(value)
                copied.append([key, value])
                previous = key
            for key, value in copied:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            log.append({"version": expected, "writes": copied})
        engine = cls(data)
        engine._base_version = base
        engine._version = base + len(log)
        engine._log = log
        return engine


class _Transaction:
    def __init__(self, engine, version, snapshot):
        self._engine = engine
        self._version = version
        self._snapshot = snapshot
        self._writes = {}
        self._reads = set()
        self._ranges = set()
        self._savepoints = []
        self._closed = False

    def _open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._open()
        _key(key)
        self._reads.add(key)
        return self._writes.get(key, self._snapshot.get(key))

    def scan(self, prefix=""):
        self._open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._ranges.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._open()
        _key(key)
        _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._open()
        _key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._open()
        _key(name)
        if any(existing == name for existing, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _key(name)
        for index, (existing, _) in enumerate(self._savepoints):
            if existing == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        self._open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._open()
        self._closed = True
        engine = self._engine
        watched = self._reads | self._writes.keys()
        for record in engine._log:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if key in watched or any(key.startswith(prefix) for prefix in self._ranges):
                    raise Conflict("committed write conflicts with transaction")
        if self._writes:
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
            engine._version += 1
            engine._log.append({"version": engine._version, "writes": writes})
        return engine._version

    def abort(self):
        self._open()
        self._closed = True
        self._writes.clear()
        self._savepoints.clear()
        self._reads.clear()
        self._ranges.clear()
