"""In-memory snapshot transactions with serializable validation and replay."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's reads or pending writes."""


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_name(name):
    if not isinstance(name, str) or not name:
        raise ValueError("keys and savepoint names must be nonempty strings")


def _validate_value(value):
    if not _is_integer(value):
        raise ValueError("stored values must be integers, excluding bool")


def _copy_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    result = {}
    for key, value in data.items():
        _validate_name(key)
        _validate_value(value)
        result[key] = value
    return result


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
        return _Transaction(self)

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if (
            not _is_integer(version)
            or not self._base_version <= version <= self._version
        ):
            raise ValueError("version is outside the retained log")
        # Contiguous versions make the first newer record's index exact.
        return [
            {
                "version": record["version"],
                "writes": [pair.copy() for pair in record["writes"]],
            }
            for record in self._records[version - self._base_version :]
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "version", "data"
        }:
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        if not _is_integer(base_version) or base_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        data = _copy_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and detach the entire log before replaying any record.
        retained = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("records must contain exactly version and writes")
            version = record["version"]
            if not _is_integer(version) or version != expected_version:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")
            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("writes must be [key, value] lists")
                key, value = pair
                _validate_name(key)
                if value is not None:
                    _validate_value(value)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                copied_writes.append([key, value])
                previous_key = key
            retained.append({"version": version, "writes": copied_writes})
            expected_version += 1

        for record in retained:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls(data)
        engine._base_version = base_version
        engine._version = expected_version - 1
        engine._records = retained
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
        _validate_name(key)
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._check_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        visible = {
            key: value
            for key, value in self._snapshot.items()
            if key.startswith(prefix)
        }
        for key, value in self._writes.items():
            if key.startswith(prefix):
                if value is None:
                    visible.pop(key, None)
                else:
                    visible[key] = value
        self._prefixes.add(prefix)
        return {key: visible[key] for key in sorted(visible)}

    def put(self, key, value):
        self._check_open()
        _validate_name(key)
        _validate_value(value)
        self._writes[key] = value

    def delete(self, key):
        self._check_open()
        _validate_name(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._check_open()
        _validate_name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _validate_name(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._check_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1 :]

    def release(self, name):
        self._check_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._check_open()
        engine = self._engine
        watched_keys = self._reads.union(self._writes)
        start = self._snapshot_version - engine._base_version
        for index in range(start, len(engine._records)):
            for key, _ in engine._records[index]["writes"]:
                if key in watched_keys or any(
                    key.startswith(prefix) for prefix in self._prefixes
                ):
                    self._close()
                    raise Conflict("concurrent write to {!r}".format(key))

        if self._writes:
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            data = engine._data.copy()
            for key, value in writes:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            version = engine._version + 1
            record = {"version": version, "writes": writes}
            engine._data = data
            engine._records.append(record)
            engine._version = version
        self._close()
        return engine.version

    def abort(self):
        self._check_open()
        self._close()
