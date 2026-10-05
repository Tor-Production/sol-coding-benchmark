"""In-memory snapshot transactions with serializable validation and log replay."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's observations or writes."""


def _validate_key(key, label="key"):
    if not isinstance(key, str) or not key:
        raise ValueError(f"{label} must be a nonempty string")


def _validate_integer(value, label="value"):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer (not bool)")


def _copy_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    for key, value in data.items():
        _validate_key(key)
        _validate_integer(value)
    return dict(data)


def _copy_record(record):
    return {
        "version": record["version"],
        "writes": [list(pair) for pair in record["writes"]],
    }


def _apply_writes(data, writes):
    for key, value in writes:
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value


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
        _validate_integer(version, "version")
        if not self._base_version <= version <= self._version:
            raise ValueError("version is outside the retained log")
        # Each retained record represents exactly one version after the base.
        return [
            _copy_record(record)
            for record in self._records[version - self._base_version :]
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        _validate_integer(base_version, "checkpoint version")
        if base_version < 0:
            raise ValueError("checkpoint version must be nonnegative")
        data = _copy_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Copy and validate the entire log before constructing or replaying it.
        validated = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("record must contain exactly version and writes")
            version = record["version"]
            _validate_integer(version, "record version")
            if version != expected_version:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a [key, value] list")
                key, value = pair
                _validate_key(key)
                if value is not None:
                    _validate_integer(value)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                copied_writes.append([key, value])
                previous_key = key
            validated.append({"version": version, "writes": copied_writes})
            expected_version += 1

        engine = cls(data)
        engine._base_version = base_version
        engine._version = base_version
        for record in validated:
            _apply_writes(engine._data, record["writes"])
            engine._version = record["version"]
        engine._records = validated
        return engine


class _Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._snapshot_version = engine._version
        self._snapshot = dict(engine._data)
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
        _validate_key(key)
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        visible = {
            key: value
            for key, value in self._snapshot.items()
            if key.startswith(prefix)
        }
        _apply_writes(
            visible,
            ((key, value) for key, value in self._writes.items() if key.startswith(prefix)),
        )
        self._prefixes.add(prefix)
        return {key: visible[key] for key in sorted(visible)}

    def put(self, key, value):
        self._ensure_open()
        _validate_key(key)
        _validate_integer(value)
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _validate_key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        _validate_key(name, "savepoint name")
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, dict(self._writes)))

    def _savepoint_index(self, name):
        _validate_key(name, "savepoint name")
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint name")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = dict(self._savepoints[index][1])
        del self._savepoints[index + 1 :]
        # Reads are never rolled back: they may already have informed a write.

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._ensure_open()
        engine = self._engine
        touched = self._reads | self._writes.keys()
        since = self._snapshot_version - engine._base_version
        for record in engine._records[since:]:
            for key, _ in record["writes"]:
                if key in touched or any(key.startswith(prefix) for prefix in self._prefixes):
                    self._close()
                    raise Conflict(f"committed write to {key!r} conflicts with transaction")

        if self._writes:
            record = {
                "version": engine._version + 1,
                "writes": [[key, self._writes[key]] for key in sorted(self._writes)],
            }
            _apply_writes(engine._data, record["writes"])
            engine._records.append(record)
            engine._version = record["version"]
        self._close()
        return engine._version

    def abort(self):
        self._ensure_open()
        self._close()
