"""A small in-memory serializable MVCC engine.

Transactions read from a private snapshot.  The engine retains the keys from
every commit so that optimistic validation observes historical writes, rather
than merely comparing the transaction's snapshot with the current values.
"""


class Conflict(RuntimeError):
    """Raised when a transaction cannot be serialized at commit time."""


def _valid_key(key):
    return isinstance(key, str) and bool(key)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(version):
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 0
    )


def _copy_and_validate_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")

    copied = {}
    for key, value in data.items():
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("invalid key or value")
        copied[key] = value
    return copied


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        else:
            data = _copy_and_validate_data(initial)

        self._data = data
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if (
            not _valid_version(version)
            or version < self._base_version
            or version > self._version
        ):
            raise ValueError("version is outside retained history")

        return [
            {
                "version": record["version"],
                "writes": [write.copy() for write in record["writes"]],
            }
            for record in self._log
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        # Validate and copy the complete recovery input before constructing or
        # mutating the returned engine.
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "version",
            "data",
        }:
            raise ValueError("invalid checkpoint")

        base_version = checkpoint["version"]
        if not _valid_version(base_version):
            raise ValueError("invalid checkpoint version")
        data = _copy_and_validate_data(checkpoint["data"])

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        copied_records = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {
                "version",
                "writes",
            }:
                raise ValueError("invalid log record")
            if (
                not _valid_version(record["version"])
                or record["version"] != expected_version
            ):
                raise ValueError("log versions must be contiguous")

            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")

            copied_writes = []
            previous_key = None
            for write in writes:
                if not isinstance(write, list) or len(write) != 2:
                    raise ValueError("each write must be a two-item list")
                key, value = write
                if not _valid_key(key):
                    raise ValueError("invalid write key")
                if value is not None and not _valid_value(value):
                    raise ValueError("invalid write value")
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and sorted")
                copied_writes.append([key, value])
                previous_key = key

            copied_records.append(
                {"version": expected_version, "writes": copied_writes}
            )
            expected_version += 1

        # Replay only after every checkpoint and record field has passed
        # validation.  Applying to the private copy also prevents input aliasing.
        restored_data = data.copy()
        for record in copied_records:
            for key, value in record["writes"]:
                if value is None:
                    restored_data.pop(key, None)
                else:
                    restored_data[key] = value

        engine = cls.__new__(cls)
        engine._data = restored_data
        engine._version = base_version + len(copied_records)
        engine._base_version = base_version
        engine._log = copied_records
        return engine


class _Transaction:
    def __init__(self, engine, snapshot_version, snapshot):
        self._engine = engine
        self._snapshot_version = snapshot_version
        self._snapshot = snapshot
        self._writes = {}
        self._read_keys = set()
        self._scan_prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")

        self._read_keys.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")

        self._scan_prefixes.add(prefix)
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
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("invalid key or value")
        self._writes[key] = value
        return None

    def delete(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._writes[key] = None
        return None

    def savepoint(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint name")

        self._savepoints.append((name, self._writes.copy()))
        return None

    def _savepoint_index(self, name):
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        self._savepoints = self._savepoints[: index + 1]
        return None

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]
        return None

    def _has_conflict(self):
        point_keys = self._read_keys | set(self._writes)
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in point_keys or any(
                    key.startswith(prefix) for prefix in self._scan_prefixes
                ):
                    return True
        return False

    def _close(self):
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._read_keys.clear()
        self._scan_prefixes.clear()
        self._savepoints.clear()

    def commit(self):
        self._ensure_open()
        engine = self._engine

        if self._has_conflict():
            self._close()
            raise Conflict("transaction conflicts with a committed write")

        if not self._writes:
            version = engine._version
            self._close()
            return version

        sorted_writes = [[key, self._writes[key]] for key in sorted(self._writes)]
        new_version = engine._version + 1

        # All validation precedes this point.  The values are primitive, so
        # applying the writes and appending their history cannot expose aliases.
        for key, value in sorted_writes:
            if value is None:
                engine._data.pop(key, None)
            else:
                engine._data[key] = value
        engine._version = new_version
        engine._log.append({"version": new_version, "writes": sorted_writes})

        self._close()
        return new_version

    def abort(self):
        self._ensure_open()
        self._close()
        return None
