"""An in-memory, serializable MVCC store with replayable commit records."""


class Conflict(RuntimeError):
    pass


def _key(value):
    if not isinstance(value, str) or not value:
        raise ValueError("key must be a nonempty string")


def _value(value):
    if type(value) is not int:
        raise ValueError("value must be an integer")


def _version(value):
    if type(value) is not int or value < 0:
        raise ValueError("version must be a nonnegative integer")


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        if not isinstance(initial, dict):
            raise ValueError("initial data must be a dict")
        for key, value in initial.items():
            _key(key)
            _value(value)
        self._data = dict(initial)
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
        _version(version)
        if version < self._base_version or version > self._version:
            raise ValueError("version outside retained history")
        return [{"version": record["version"],
                 "writes": [pair.copy() for pair in record["writes"]]}
                for record in self._log if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        _version(base)
        data = checkpoint["data"]
        if not isinstance(data, dict):
            raise ValueError("invalid checkpoint data")
        engine = cls(data)
        if not isinstance(records, list):
            raise ValueError("records must be a list")
        replay = []
        for expected, record in enumerate(records, base + 1):
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            if type(record["version"]) is not int or record["version"] != expected:
                raise ValueError("noncontiguous record version")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("invalid writes")
            copied = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid write pair")
                key, value = pair
                _key(key)
                if previous is not None and key <= previous:
                    raise ValueError("writes must have sorted unique keys")
                if value is not None:
                    _value(value)
                copied.append([key, value])
                previous = key
            replay.append({"version": expected, "writes": copied})
        for record in replay:
            for key, value in record["writes"]:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
        engine._base_version = base
        engine._version = base + len(replay)
        engine._log = replay
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

    def _check_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._check_open()
        _key(key)
        self._reads.add(key)
        return self._writes.get(key, self._snapshot.get(key))

    def scan(self, prefix=""):
        self._check_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._prefixes.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible) if key.startswith(prefix)}

    def put(self, key, value):
        self._check_open()
        _key(key)
        _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._check_open()
        _key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._check_open()
        _key(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _key(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._check_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        self._check_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._check_open()
        self._closed = True
        engine = self._engine
        for record in engine._log:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if (key in self._reads or key in self._writes or
                        any(key.startswith(prefix) for prefix in self._prefixes)):
                    raise Conflict("transaction conflicts with committed write")
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
        self._check_open()
        self._closed = True
        self._writes.clear()
        self._savepoints.clear()
        self._reads.clear()
        self._prefixes.clear()
