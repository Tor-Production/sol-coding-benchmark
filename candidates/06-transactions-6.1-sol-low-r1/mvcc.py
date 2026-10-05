"""Snapshot transactions with conservative serializable commit validation."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's observations."""


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected a nonempty string")


def _data(value):
    if not isinstance(value, dict):
        raise ValueError("expected a data dict")
    for key, item in value.items():
        _name(key)
        if not _integer(item):
            raise ValueError("stored values must be integers")
    return dict(value)


def _copy_record(record):
    return {"version": record["version"],
            "writes": [pair[:] for pair in record["writes"]]}

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
        return _Transaction(self)

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        if (not _integer(version) or
                not self._base_version <= version <= self._version):
            raise ValueError("version outside retained history")
        return [_copy_record(record) for record in self._records
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict) or
                set(checkpoint) != {"version", "data"}):
            raise ValueError("invalid checkpoint")
        version = checkpoint["version"]
        if not _integer(version) or version < 0:
            raise ValueError("invalid checkpoint version")
        data = _data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")
        validated = []
        for expected, record in enumerate(records, version + 1):
            if (not isinstance(record, dict) or
                    set(record) != {"version", "writes"} or
                    not _integer(record["version"]) or
                    record["version"] != expected):
                raise ValueError("invalid record version or fields")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("writes must contain two-element lists")
                key, value = pair
                _name(key)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be strictly sorted")
                if value is not None and not _integer(value):
                    raise ValueError("invalid write value")
                previous = key
            validated.append(_copy_record(record))
        engine = cls(data)
        engine._base_version = version
        engine._version = version + len(validated)
        engine._records = validated
        for record in validated:
            for key, value in record["writes"]:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
        return engine


class _Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._version = engine.version
        self._snapshot = engine._data.copy()
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
        _name(key)
        self._reads.add(key)
        return self._writes[key] if key in self._writes else self._snapshot.get(key)

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
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._check_open()
        _name(key)
        if not _integer(value):
            raise ValueError("stored values must be integers")
        self._writes[key] = value

    def delete(self, key):
        self._check_open()
        _name(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._check_open()
        _name(name)
        if any(saved == name for saved, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        self._check_open()
        _name(name)
        for index, (saved, _) in enumerate(self._savepoints):
            if saved == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def _close(self):
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()

    def commit(self):
        self._check_open()
        engine = self._engine
        watched = self._reads | self._writes.keys()
        for record in engine._records:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if key in watched or any(key.startswith(p) for p in self._prefixes):
                    self._close()
                    raise Conflict("committed write conflicts with transaction")
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
        self._check_open()
        self._close()
