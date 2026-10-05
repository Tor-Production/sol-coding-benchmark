"""In-memory snapshot transactions with serializable commit validation."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's reads or writes."""


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
    for key, value in data.items():
        _validate_name(key)
        _validate_value(value)
    return dict(data)


def _copy_record(record):
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
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
        if (
            not _is_integer(version)
            or not self._base_version <= version <= self._version
        ):
            raise ValueError("version must be within the retained log")
        # Every retained version corresponds to exactly one write record.
        return [
            _copy_record(record)
            for record in self._records[version - self._base_version :]
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        if not _is_integer(base_version) or base_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        data = _copy_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and copy the entire log before replaying any of it.
        copied_records = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("records must contain exactly version and writes")
            version = record["version"]
            if not _is_integer(version) or version != expected_version:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a two-element list")
                key, value = pair
                _validate_name(key)
                if value is not None:
                    _validate_value(value)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                copied_writes.append([key, value])
                previous_key = key
            copied_records.append({"version": version, "writes": copied_writes})
            expected_version += 1

        for record in copied_records:
            _apply_writes(data, record["writes"])
        engine = cls(data)
        engine._base_version = base_version
        engine._version = expected_version - 1
        engine._records = copied_records
        return engine

    def _commit(self, transaction):
        touched_keys = transaction._reads | transaction._writes.keys()
        first_record = transaction._snapshot_version - self._base_version
        for index in range(first_record, len(self._records)):
            for key, _ in self._records[index]["writes"]:
                if key in touched_keys or any(
                    key.startswith(prefix) for prefix in transaction._prefixes
                ):
                    raise Conflict("committed write conflicts with key {!r}".format(key))

        if transaction._writes:
            writes = [
                [key, transaction._writes[key]] for key in sorted(transaction._writes)
            ]
            data = self._data.copy()
            _apply_writes(data, writes)
            version = self._version + 1
            self._records.append({"version": version, "writes": writes})
            self._data = data
            self._version = version
        return self._version


class _Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._snapshot = engine._data.copy()
        self._snapshot_version = engine.version
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
        _validate_name(key)
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        visible = self._snapshot.copy()
        _apply_writes(visible, self._writes.items())
        self._prefixes.add(prefix)
        return {key: visible[key] for key in sorted(visible) if key.startswith(prefix)}

    def put(self, key, value):
        self._ensure_open()
        _validate_name(key)
        _validate_value(value)
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _validate_name(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
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
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1 :]

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def _close(self):
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()
        self._engine = None

    def commit(self):
        self._ensure_open()
        try:
            return self._engine._commit(self)
        finally:
            # Both successful and conflicting commits permanently close it.
            self._close()

    def abort(self):
        self._ensure_open()
        self._close()
