class Conflict(RuntimeError):
    """A transaction's dependencies were touched after its snapshot."""


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected a nonempty string")


def _integer(value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected an integer, excluding bool")


def _data_copy(data):
    if not isinstance(data, dict):
        raise ValueError("expected a data dictionary")
    for key, value in data.items():
        _name(key)
        _integer(value)
    return data.copy()


def _record_copy(record):
    return {"version": record["version"],
            "writes": [[key, value] for key, value in record["writes"]]}


class Engine:
    """Snapshot transactions with serializable history validation."""

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
        _integer(version)
        if not self._base_version <= version <= self._version:
            raise ValueError("version is outside retained history")
        return [_record_copy(record) for record in self._records
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint fields")
        base = checkpoint["version"]
        _integer(base)
        if base < 0:
            raise ValueError("checkpoint version must be nonnegative")
        data = _data_copy(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        validated = []
        for expected, record in enumerate(records, base + 1):
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record fields")
            _integer(record["version"])
            if record["version"] != expected:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a two-element list")
                key, value = pair
                _name(key)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    _integer(value)
                previous = key
            validated.append(_record_copy(record))

        # Only replay after validating the complete recovery input.
        for record in validated:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
        engine = cls(data)
        engine._base_version = base
        engine._version = base + len(validated)
        engine._records = validated
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
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

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
        _integer(value)
        self._writes[key] = value

    def delete(self, key):
        self._check_open()
        _name(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._check_open()
        _name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint name")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _name(name)
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
        dependencies = self._reads | self._writes.keys()
        for record in engine._records:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if key in dependencies or any(key.startswith(p) for p in self._prefixes):
                    self._close()
                    raise Conflict("a dependency was written after the snapshot")

        if self._writes:
            record = {"version": engine.version + 1,
                      "writes": [[key, self._writes[key]] for key in sorted(self._writes)]}
            for key, value in record["writes"]:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
            engine._records.append(record)
            engine._version += 1
        self._close()
        return engine.version

    def abort(self):
        self._check_open()
        self._close()
