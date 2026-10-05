"""In-memory serializable MVCC with savepoints and validated log replay."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's snapshot dependencies."""


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected a nonempty string")


def _value(value):
    if not _integer(value):
        raise ValueError("stored values must be integers, excluding bool")


def _data_copy(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    result = {}
    for key, value in data.items():
        _name(key)
        _value(value)
        result[key] = value
    return result


def _apply(data, writes):
    for key, value in writes:
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _data_copy(initial)
        self._version = 0
        self._base_version = 0
        # Each entry is (version, immutable tuple of (key, value) pairs).
        # Contiguous versions make snapshot-relative indexing possible.
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self)

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if (not _integer(version)
                or not self._base_version <= version <= self._version):
            raise ValueError("version is outside the retained log")
        return [
            {"version": number, "writes": [[key, value] for key, value in writes]}
            for number, writes in self._records[version - self._base_version:]
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) != {"version", "data"}):
            raise ValueError("checkpoint must contain exactly version and data")
        base = checkpoint["version"]
        if not _integer(base) or base < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        data = _data_copy(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        validated = []
        for expected, record in enumerate(records, base + 1):
            if (not isinstance(record, dict)
                    or set(record) != {"version", "writes"}):
                raise ValueError("record must contain exactly version and writes")
            number = record["version"]
            if not _integer(number) or number != expected:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a two-element list")
                key, value = pair
                _name(key)
                if value is not None:
                    _value(value)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be unique and strictly sorted")
                copied.append((key, value))
                previous = key
            validated.append((number, tuple(copied)))

        # No input is accepted until the entire checkpoint and log is valid.
        for _, writes in validated:
            _apply(data, writes)
        engine = cls(data)
        engine._base_version = base
        engine._version = base + len(validated)
        engine._records = validated
        return engine


class _Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._snapshot_version = engine.version
        self._snapshot = engine._data.copy()
        self._writes = {}
        self._reads = set()
        self._prefixes = set()
        self._savepoints = []
        self._closed = False

    def _check_open(self):
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
        _apply(visible, self._writes.items())
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._check_open()
        _name(key)
        _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._check_open()
        _name(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._check_open()
        _name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
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

    def commit(self):
        self._check_open()
        engine = self._engine
        dependencies = self._reads | self._writes.keys()
        offset = self._snapshot_version - engine._base_version
        for _, writes in engine._records[offset:]:
            for key, _ in writes:
                if (key in dependencies
                        or any(key.startswith(prefix) for prefix in self._prefixes)):
                    self._close()
                    raise Conflict("a committed write touched a transaction dependency")

        if self._writes:
            writes = tuple(sorted(self._writes.items()))
            data = engine._data.copy()
            _apply(data, writes)
            number = engine._version + 1
            engine._data = data
            engine._records.append((number, writes))
            engine._version = number
        self._close()
        return engine.version

    def abort(self):
        self._check_open()
        self._close()
