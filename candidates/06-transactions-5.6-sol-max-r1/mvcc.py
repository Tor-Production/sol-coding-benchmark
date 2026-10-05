"""A small in-memory MVCC engine with serializable validation.

Transactions read from a private snapshot and validate their read and write
sets against the engine's commit log. The log is also the recovery format.
"""


class Conflict(RuntimeError):
    """Raised when a transaction cannot be serialized at commit time."""


def _require_key(key):
    if not isinstance(key, str) or not key:
        raise ValueError("keys must be nonempty strings")


def _require_name(name):
    if not isinstance(name, str) or not name:
        raise ValueError("savepoint names must be nonempty strings")


def _require_prefix(prefix):
    if not isinstance(prefix, str):
        raise ValueError("prefixes must be strings")


def _require_value(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("stored values must be integers")


def _require_version(version):
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("versions must be integers")


def _copy_valid_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")

    copied = {}
    for key, value in data.items():
        _require_key(key)
        _require_value(value)
        copied[key] = value
    return copied


def _has_exact_keys(mapping, expected):
    return isinstance(mapping, dict) and set(mapping) == expected


def _copy_records(records):
    """Return a defensive copy of canonical internal log records."""
    return [
        {
            "version": record["version"],
            "writes": [[key, value] for key, value in record["writes"]],
        }
        for record in records
    ]


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        else:
            data = _copy_valid_data(initial)

        self._data = data
        self._version = 0
        self._base_version = 0
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        data = {key: self._data[key] for key in sorted(self._data)}
        return {"version": self._version, "data": data}

    def log_since(self, version):
        _require_version(version)
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside retained history")

        # Records are contiguous, so this is also the number of records at or
        # before the requested version.
        start = version - self._base_version
        return _copy_records(self._records[start:])

    @classmethod
    def restore(cls, checkpoint, records):
        if not _has_exact_keys(checkpoint, {"version", "data"}):
            raise ValueError("invalid checkpoint")

        checkpoint_version = checkpoint["version"]
        _require_version(checkpoint_version)
        if checkpoint_version < 0:
            raise ValueError("checkpoint version must be nonnegative")
        checkpoint_data = _copy_valid_data(checkpoint["data"])

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and copy every record before replaying any of them.
        validated_records = []
        expected_version = checkpoint_version + 1
        for record in records:
            if not _has_exact_keys(record, {"version", "writes"}):
                raise ValueError("invalid log record")

            record_version = record["version"]
            _require_version(record_version)
            if record_version != expected_version:
                raise ValueError("log versions must be contiguous")

            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            validated_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("writes must contain [key, value] pairs")
                key, value = pair
                _require_key(key)
                if previous_key is not None and not previous_key < key:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    _require_value(value)
                validated_writes.append([key, value])
                previous_key = key

            validated_records.append(
                {"version": record_version, "writes": validated_writes}
            )
            expected_version += 1

        recovered_data = checkpoint_data.copy()
        for record in validated_records:
            for key, value in record["writes"]:
                if value is None:
                    recovered_data.pop(key, None)
                else:
                    recovered_data[key] = value

        engine = cls(recovered_data)
        engine._version = expected_version - 1
        engine._base_version = checkpoint_version
        engine._records = validated_records
        return engine

    def _conflicts(self, snapshot_version, keys, prefixes):
        for record in self._records:
            if record["version"] <= snapshot_version:
                continue
            for key, _value in record["writes"]:
                if key in keys or any(key.startswith(prefix) for prefix in prefixes):
                    return True
        return False

    def _commit_writes(self, writes):
        if not writes:
            return self._version

        ordered_writes = [[key, writes[key]] for key in sorted(writes)]
        new_data = self._data.copy()
        for key, value in ordered_writes:
            if value is None:
                new_data.pop(key, None)
            else:
                new_data[key] = value

        new_version = self._version + 1
        record = {"version": new_version, "writes": ordered_writes}

        # Build all replacement objects first, then publish the commit.
        new_records = self._records + [record]
        self._data = new_data
        self._records = new_records
        self._version = new_version
        return new_version


class Transaction:
    def __init__(self, engine, snapshot_version, snapshot):
        self._engine = engine
        self._snapshot_version = snapshot_version
        self._snapshot = snapshot
        self._writes = {}
        self._read_keys = set()
        self._read_prefixes = set()
        self._savepoints = []
        self._open = True

    def _ensure_open(self):
        if not self._open:
            raise RuntimeError("transaction is closed")

    def _close(self):
        self._open = False
        self._snapshot = {}
        self._writes = {}
        self._read_keys = set()
        self._read_prefixes = set()
        self._savepoints = []

    def get(self, key):
        self._ensure_open()
        _require_key(key)

        self._read_keys.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        _require_prefix(prefix)

        self._read_prefixes.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {
            key: visible[key]
            for key in sorted(visible)
            if key.startswith(prefix)
        }

    def put(self, key, value):
        self._ensure_open()
        _require_key(key)
        _require_value(value)
        self._writes[key] = value
        return None

    def delete(self, key):
        self._ensure_open()
        _require_key(key)
        self._writes[key] = None
        return None

    def savepoint(self, name):
        self._ensure_open()
        _require_name(name)
        if any(saved_name == name for saved_name, _writes in self._savepoints):
            raise ValueError("duplicate savepoint name")

        saved_writes = self._writes.copy()
        self._savepoints.append((name, saved_writes))
        return None

    def rollback_to(self, name):
        self._ensure_open()
        _require_name(name)

        index = self._savepoint_index(name)
        restored_writes = self._savepoints[index][1].copy()
        retained_savepoints = self._savepoints[: index + 1]
        self._writes = restored_writes
        self._savepoints = retained_savepoints
        return None

    def release(self, name):
        self._ensure_open()
        _require_name(name)

        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]
        return None

    def _savepoint_index(self, name):
        for index, (saved_name, _writes) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def commit(self):
        self._ensure_open()

        conflict_keys = self._read_keys | self._writes.keys()
        if self._engine._conflicts(
            self._snapshot_version, conflict_keys, self._read_prefixes
        ):
            self._close()
            raise Conflict("transaction conflicts with a committed write")

        version = self._engine._commit_writes(self._writes)
        self._close()
        return version

    def abort(self):
        self._ensure_open()
        self._close()
        return None
