"""In-memory snapshot transactions with serializable validation and replay."""


class Conflict(RuntimeError):
    """A committed write invalidated a transaction's snapshot."""


def _validate_name(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + " must be a nonempty string")


def _validate_integer(value, label):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(label + " must be an integer other than bool")


def _copy_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    copied = {}
    for key, value in data.items():
        _validate_name(key, "key")
        _validate_integer(value, "value")
        copied[key] = value
    return copied


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
        _validate_integer(version, "version")
        if not self._base_version <= version <= self._version:
            raise ValueError("version is outside the retained log")
        return [
            _copy_record(record)
            for record in self._records[version - self._base_version:]
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

        # Validate and copy the entire log before replaying any of it.
        copied_records = []
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
                _validate_name(key, "key")
                if value is not None:
                    _validate_integer(value, "value")
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


class _Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._snapshot_version = engine._version
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
        _validate_name(key, "key")
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
        _apply_writes(visible, self._writes.items())
        return {key: visible[key] for key in sorted(visible) if key.startswith(prefix)}

    def put(self, key, value):
        self._check_open()
        _validate_name(key, "key")
        _validate_integer(value, "value")
        self._writes[key] = value

    def delete(self, key):
        self._check_open()
        _validate_name(key, "key")
        self._writes[key] = None

    def savepoint(self, name):
        self._check_open()
        _validate_name(name, "savepoint name")
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        _validate_name(name, "savepoint name")
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
        watched_keys = self._reads.union(self._writes)
        # History is essential: comparing present values would miss ABA writes
        # and phantoms inserted and deleted since the snapshot.
        start = self._snapshot_version - engine._base_version
        for record in engine._records[start:]:
            for key, _ in record["writes"]:
                if key in watched_keys or any(key.startswith(p) for p in self._prefixes):
                    self._close()
                    raise Conflict("committed write conflicts with transaction")

        if self._writes:
            record = {
                "version": engine._version + 1,
                "writes": [[key, self._writes[key]] for key in sorted(self._writes)],
            }
            _apply_writes(engine._data, record["writes"])
            engine._records.append(record)
            engine._version = record["version"]
        version = engine._version
        self._close()
        return version

    def abort(self):
        self._check_open()
        self._close()
