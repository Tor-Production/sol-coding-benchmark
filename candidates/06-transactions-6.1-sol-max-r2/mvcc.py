"""An in-memory snapshot store with optimistic serializable transactions."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's snapshot dependencies."""


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_key(key):
    if not isinstance(key, str) or not key:
        raise ValueError("keys and savepoint names must be nonempty strings")


def _validate_value(value):
    if not _is_integer(value):
        raise ValueError("stored values must be integers, excluding bool")


def _copy_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    copied = {}
    for key, value in data.items():
        _validate_key(key)
        _validate_value(value)
        copied[key] = value
    return copied


def _copy_records(records):
    return [
        {
            "version": record["version"],
            "writes": [[key, value] for key, value in record["writes"]],
        }
        for record in records
    ]


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _copy_data(initial)
        self._version = 0
        self._base_version = 0
        self._log = []

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
        if (not _is_integer(version)
                or not self._base_version <= version <= self._version):
            raise ValueError("version is outside the retained log")
        # Versions are contiguous, and the base checkpoint has no log entry.
        return _copy_records(self._log[version - self._base_version:])

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) != {"version", "data"}):
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        if not _is_integer(base_version) or base_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        data = _copy_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        validated_records = []
        next_version = base_version + 1
        for record in records:
            if (not isinstance(record, dict)
                    or set(record) != {"version", "writes"}):
                raise ValueError("records must contain exactly version and writes")
            version = record["version"]
            if not _is_integer(version) or version != next_version:
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
                _validate_key(key)
                if value is not None:
                    _validate_value(value)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and sorted")
                copied_writes.append([key, value])
                previous_key = key
            validated_records.append({"version": version, "writes": copied_writes})
            next_version += 1

        # Validate and detach the entire input before replaying any record.
        engine = cls(data)
        for record in validated_records:
            engine._apply_writes(engine._data, record["writes"])
        engine._base_version = base_version
        engine._version = next_version - 1
        engine._log = validated_records
        return engine

    @staticmethod
    def _apply_writes(data, writes):
        for key, value in writes:
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value

    def _commit(self, transaction):
        dependencies = transaction._reads | transaction._writes.keys()
        start = transaction._snapshot_version - self._base_version
        for record in self._log[start:]:
            for key, _ in record["writes"]:
                if (key in dependencies
                        or any(key.startswith(prefix)
                               for prefix in transaction._prefixes)):
                    raise Conflict("a committed write touched a transaction dependency")

        if transaction._writes:
            writes = [[key, transaction._writes[key]]
                      for key in sorted(transaction._writes)]
            data = self._data.copy()
            self._apply_writes(data, writes)
            version = self._version + 1
            record = {"version": version, "writes": writes}
            self._log.append(record)
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
        _validate_key(key)
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._prefixes.add(prefix)
        visible = self._snapshot.copy()
        self._engine._apply_writes(visible, self._writes.items())
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._ensure_open()
        _validate_key(key)
        _validate_value(value)
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _validate_key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        _validate_key(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _validate_key(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

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

    def commit(self):
        self._ensure_open()
        try:
            return self._engine._commit(self)
        finally:
            self._close()

    def abort(self):
        self._ensure_open()
        self._close()
