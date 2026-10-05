"""An in-memory MVCC store with optimistic serializable validation."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's snapshot."""


def _key(value):
    if not isinstance(value, str) or not value:
        raise ValueError("key must be a nonempty string")


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("savepoint name must be a nonempty string")


def _value(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("stored value must be an integer")


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("version must be a nonnegative integer")


def _data_copy(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    copied = {}
    for key, value in data.items():
        _key(key)
        _value(value)
        copied[key] = value
    return copied


def _record_copy(record):
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        data = {} if initial is None else _data_copy(initial)
        self._data = data
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._data.copy(), self._version)

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        _version(version)
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside the retained log")
        return [_record_copy(record) for record in self._log[version - self._base_version:]]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or checkpoint.keys() != {"version", "data"}:
            raise ValueError("checkpoint must have exactly version and data")
        base_version = checkpoint["version"]
        _version(base_version)
        data = _data_copy(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        log = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or record.keys() != {"version", "writes"}:
                raise ValueError("record must have exactly version and writes")
            version = record["version"]
            _version(version)
            if version != expected_version:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a [key, value] pair")
                key, value = pair
                _key(key)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    _value(value)
                copied_writes.append([key, value])
                previous_key = key

            for key, value in copied_writes:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            log.append({"version": version, "writes": copied_writes})
            expected_version += 1

        engine = cls()
        engine._data = data
        engine._version = expected_version - 1
        engine._base_version = base_version
        engine._log = log
        return engine


class _Transaction:
    def __init__(self, engine, snapshot, version):
        self._engine = engine
        self._snapshot = snapshot
        self._snapshot_version = version
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
        _name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name already exists")
        self._savepoints.append((name, self._writes.copy()))

    def rollback_to(self, name):
        self._ensure_open()
        _name(name)
        for index, (saved_name, writes) in enumerate(self._savepoints):
            if saved_name == name:
                self._writes = writes.copy()
                del self._savepoints[index + 1:]
                return
        raise ValueError("unknown savepoint")

    def release(self, name):
        self._ensure_open()
        _name(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                del self._savepoints[index:]
                return
        raise ValueError("unknown savepoint")

    def commit(self):
        self._ensure_open()
        engine = self._engine
        touched = self._reads | self._writes.keys()
        for record in engine._log[self._snapshot_version - engine._base_version:]:
            for key, _ in record["writes"]:
                if key in touched or any(key.startswith(prefix) for prefix in self._prefixes):
                    self._closed = True
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
        self._closed = True
        return engine._version

    def abort(self):
        self._ensure_open()
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()
        self._closed = True
