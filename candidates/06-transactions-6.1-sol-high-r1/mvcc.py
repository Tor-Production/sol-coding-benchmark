"""In-memory serializable optimistic transactions and validated log replay."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's snapshot dependencies."""


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected a nonempty string")


def _value(value):
    if not _integer(value):
        raise ValueError("expected an integer value")


def _data_copy(data):
    if not isinstance(data, dict):
        raise ValueError("expected a data dictionary")
    for key, value in data.items():
        _name(key)
        _value(value)
    return dict(data)


def _record_copy(record):
    return {"version": record["version"],
            "writes": [pair[:] for pair in record["writes"]]}


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _data_copy(initial)
        self._version = 0
        self._base_version = 0
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self)

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        if (not _integer(version)
                or not self._base_version <= version <= self._version):
            raise ValueError("version outside retained history")
        return [_record_copy(record)
                for record in self._records[version - self._base_version:]]

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) != {"version", "data"}):
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        if not _integer(base) or base < 0:
            raise ValueError("invalid checkpoint version")
        data = _data_copy(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and detach the entire log before replaying any of it.
        validated = []
        for expected, record in enumerate(records, base + 1):
            if (not isinstance(record, dict)
                    or set(record) != {"version", "writes"}):
                raise ValueError("invalid record")
            version = record["version"]
            if not _integer(version) or version != expected:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            previous = None
            copied_writes = []
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a two-element list")
                key, value = pair
                _name(key)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be strictly sorted")
                if value is not None:
                    _value(value)
                copied_writes.append([key, value])
                previous = key
            validated.append({"version": version, "writes": copied_writes})

        engine = cls(data)
        for record in validated:
            for key, value in record["writes"]:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
        engine._base_version = base
        engine._version = base + len(validated)
        engine._records = validated
        return engine


class _Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._snapshot = dict(engine._data)
        self._version = engine.version
        self._writes = {}
        self._reads = set()
        self._prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def _close(self):
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()

    def get(self, key):
        self._ensure_open()
        _name(key)
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._prefixes.add(prefix)
        visible = {key: value for key, value in self._snapshot.items()
                   if key.startswith(prefix)}
        for key, value in self._writes.items():
            if key.startswith(prefix):
                if value is None:
                    visible.pop(key, None)
                else:
                    visible[key] = value
        return {key: visible[key] for key in sorted(visible)}

    def put(self, key, value):
        self._ensure_open()
        _name(key)
        _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _name(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        _name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, dict(self._writes)))

    def _savepoint_index(self, name):
        _name(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = dict(self._savepoints[index][1])
        del self._savepoints[index + 1:]

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._ensure_open()
        engine = self._engine
        dependencies = self._reads | self._writes.keys()
        # Retained history is contiguous, so the offset also works after restore.
        for record in engine._records[self._version - engine._base_version:]:
            for key, _ in record["writes"]:
                if (key in dependencies
                        or any(key.startswith(prefix) for prefix in self._prefixes)):
                    self._close()
                    raise Conflict("committed write touches a transaction dependency")

        if self._writes:
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
            engine._version += 1
            engine._records.append({"version": engine._version, "writes": writes})
        self._close()
        return engine.version

    def abort(self):
        self._ensure_open()
        self._close()
