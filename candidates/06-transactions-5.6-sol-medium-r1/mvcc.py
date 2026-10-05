"""A small in-memory serializable MVCC engine."""


class Conflict(RuntimeError):
    """Raised when optimistic transaction validation fails."""


def _valid_key(key):
    return isinstance(key, str) and bool(key)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    for key, value in data.items():
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("invalid key or value")


def _copy_record(record):
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        _validate_data(initial)
        self._data = dict(initial)
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, dict(self._data))

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if (not isinstance(version, int) or isinstance(version, bool) or
                version < self._base_version or version > self._version):
            raise ValueError("invalid log version")
        return [_copy_record(record) for record in self._log
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        # Validate everything and replay into local temporaries before creating
        # the returned engine, so malformed recovery input has no partial effect.
        if (not isinstance(checkpoint, dict) or
                set(checkpoint) != {"version", "data"}):
            raise ValueError("invalid checkpoint")
        base_version = checkpoint["version"]
        if (not isinstance(base_version, int) or isinstance(base_version, bool)
                or base_version < 0):
            raise ValueError("invalid checkpoint version")
        _validate_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        data = dict(checkpoint["data"])
        validated_records = []
        expected_version = base_version + 1
        for record in records:
            if (not isinstance(record, dict) or
                    set(record) != {"version", "writes"}):
                raise ValueError("invalid record")
            version = record["version"]
            if (not isinstance(version, int) or isinstance(version, bool) or
                    version != expected_version):
                raise ValueError("non-contiguous record version")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")

            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid write")
                key, value = pair
                if not _valid_key(key):
                    raise ValueError("invalid write key")
                if value is not None and not _valid_value(value):
                    raise ValueError("invalid write value")
                if previous_key is not None and key <= previous_key:
                    raise ValueError("writes are not strictly sorted")
                previous_key = key
                copied_writes.append([key, value])

            validated_records.append({
                "version": version,
                "writes": copied_writes,
            })
            expected_version += 1

        for record in validated_records:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls(dict(checkpoint["data"]))
        engine._data = data
        engine._version = expected_version - 1
        engine._base_version = base_version
        engine._log = validated_records
        return engine


class _Transaction:
    def __init__(self, engine, version, snapshot):
        self._engine = engine
        self._snapshot_version = version
        self._snapshot = snapshot
        self._writes = {}
        self._read_keys = set()
        self._scan_prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    @staticmethod
    def _check_key(key):
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")

    @staticmethod
    def _check_name(name):
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")

    def get(self, key):
        self._ensure_open()
        self._check_key(key)
        self._read_keys.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._scan_prefixes.add(prefix)
        visible = dict(self._snapshot)
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._ensure_open()
        self._check_key(key)
        if not _valid_value(value):
            raise ValueError("value must be an integer other than bool")
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        self._check_key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        self._check_name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, dict(self._writes)))

    def rollback_to(self, name):
        self._ensure_open()
        self._check_name(name)
        index = self._savepoint_index(name)
        self._writes = dict(self._savepoints[index][1])
        self._savepoints = self._savepoints[:index + 1]

    def release(self, name):
        self._ensure_open()
        self._check_name(name)
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]

    def _savepoint_index(self, name):
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def commit(self):
        self._ensure_open()
        self._closed = True

        touched = self._read_keys | set(self._writes)
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if (key in touched or
                        any(key.startswith(prefix)
                            for prefix in self._scan_prefixes)):
                    self._discard_state()
                    raise Conflict("transaction conflicts with a committed write")

        if self._writes:
            new_version = self._engine._version + 1
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            # All possible validation has completed. The following mutations
            # constitute the single logical commit.
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._version = new_version
            self._engine._log.append({"version": new_version, "writes": writes})

        result = self._engine._version
        self._discard_state()
        return result

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._discard_state()

    def _discard_state(self):
        self._snapshot = {}
        self._writes = {}
        self._read_keys = set()
        self._scan_prefixes = set()
        self._savepoints = []
