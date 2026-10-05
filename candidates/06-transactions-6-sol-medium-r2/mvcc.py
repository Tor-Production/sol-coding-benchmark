"""An in-memory optimistic MVCC store with serializable validation."""


class Conflict(RuntimeError):
    """A transaction's snapshot is no longer safe to commit."""


def _key(key):
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a nonempty string")


def _value(value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("value must be an integer")


def _data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    for key, value in data.items():
        _key(key)
        _value(value)


def _version(version):
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError("version must be a nonnegative integer")


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        _data(initial)
        self._data = dict(initial)
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
        return [{"version": record["version"],
                 "writes": [pair.copy() for pair in record["writes"]]}
                for record in self._records
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        _version(base)
        _data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        data = checkpoint["data"].copy()
        history = []
        expected = base + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            version = record["version"]
            _version(version)
            if version != expected:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied_writes = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a two-element list")
                key, value = pair
                _key(key)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    _value(value)
                    data[key] = value
                else:
                    data.pop(key, None)
                copied_writes.append([key, value])
                previous = key
            history.append({"version": version, "writes": copied_writes})
            expected += 1

        engine = cls(data)
        engine._version = expected - 1
        engine._base_version = base
        engine._records = history
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
        return {key: visible[key] for key in sorted(visible) if key.startswith(prefix)}

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
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, self._writes.copy()))

    def rollback_to(self, name):
        self._ensure_open()
        _key(name)
        for index, (saved_name, writes) in enumerate(self._savepoints):
            if saved_name == name:
                self._writes = writes.copy()
                self._savepoints = self._savepoints[:index + 1]
                return
        raise ValueError("unknown savepoint")

    def release(self, name):
        self._ensure_open()
        _key(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                self._savepoints = self._savepoints[:index]
                return
        raise ValueError("unknown savepoint")

    def commit(self):
        self._ensure_open()
        self._closed = True
        watched = self._reads | self._writes.keys()
        for record in self._engine._records:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if key in watched or any(key.startswith(prefix) for prefix in self._prefixes):
                    raise Conflict("a committed write conflicts with this transaction")

        if self._writes:
            version = self._engine._version + 1
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._records.append({"version": version, "writes": writes})
            self._engine._version = version
        return self._engine._version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()
