"""A small in-memory serializable MVCC engine.

Transactions read from a private snapshot and are validated against every
committed write made after that snapshot.  The commit log is also the recovery
format used by :meth:`Engine.restore`.
"""

from __future__ import annotations


class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""


_TOMBSTONE = object()


def _valid_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_key(key):
    if not isinstance(key, str) or not key:
        raise ValueError("keys must be nonempty strings")


def _validate_value(value):
    if not _valid_integer(value):
        raise ValueError("values must be integers (but not bool)")


def _validate_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")

    # Build the copy only after validating each entry.  The caller never keeps
    # a reference to the supplied mapping.
    copied = {}
    for key, value in data.items():
        _validate_key(key)
        _validate_value(value)
        copied[key] = value
    return copied


def _copy_record(record):
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    """An in-memory MVCC store with optimistic serializable transactions."""

    def __init__(self, initial=None):
        if initial is None:
            data = {}
        else:
            data = _validate_data(initial)

        self._data = data
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if not _valid_integer(version):
            raise ValueError("version must be an integer (but not bool)")
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside retained history")
        return [
            _copy_record(record)
            for record in self._log
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        """Validate and replay a checkpoint plus its contiguous commit log."""

        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "version",
            "data",
        }:
            raise ValueError("checkpoint must contain exactly version and data")

        base_version = checkpoint["version"]
        if not _valid_integer(base_version) or base_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        checkpoint_data = _validate_data(checkpoint["data"])

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and defensively copy the *entire* log before constructing or
        # replaying the new engine.
        validated_records = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {
                "version",
                "writes",
            }:
                raise ValueError("records must contain exactly version and writes")

            record_version = record["version"]
            if not _valid_integer(record_version) or record_version != expected_version:
                raise ValueError("record versions must be contiguous")

            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            copied_writes = []
            previous_key = None
            for index, pair in enumerate(writes):
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a [key, value] list")
                key, value = pair
                _validate_key(key)
                if value is not None:
                    _validate_value(value)
                if index and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                previous_key = key
                copied_writes.append([key, value])

            validated_records.append(
                {"version": record_version, "writes": copied_writes}
            )
            expected_version += 1

        restored_data = checkpoint_data.copy()
        for record in validated_records:
            for key, value in record["writes"]:
                if value is None:
                    restored_data.pop(key, None)
                else:
                    restored_data[key] = value

        engine = cls()
        engine._data = restored_data
        engine._base_version = base_version
        engine._version = expected_version - 1
        engine._log = [_copy_record(record) for record in validated_records]
        return engine


class Transaction:
    """A transaction over an immutable begin-time snapshot."""

    def __init__(self, engine, snapshot_version, snapshot):
        self._engine = engine
        self._snapshot_version = snapshot_version
        self._snapshot = snapshot
        self._writes = {}
        self._read_keys = set()
        self._read_prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._ensure_open()
        _validate_key(key)
        self._read_keys.add(key)
        if key in self._writes:
            value = self._writes[key]
            return None if value is _TOMBSTONE else value
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._read_prefixes.add(prefix)

        visible = {
            key: value
            for key, value in self._snapshot.items()
            if key.startswith(prefix)
        }
        for key, value in self._writes.items():
            if not key.startswith(prefix):
                continue
            if value is _TOMBSTONE:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)}

    def put(self, key, value):
        self._ensure_open()
        _validate_key(key)
        _validate_value(value)
        self._writes[key] = value
        return None

    def delete(self, key):
        self._ensure_open()
        _validate_key(key)
        self._writes[key] = _TOMBSTONE
        return None

    def savepoint(self, name):
        self._ensure_open()
        _validate_key(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))
        return None

    def _savepoint_index(self, name):
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        _validate_key(name)
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        self._savepoints = self._savepoints[: index + 1]
        return None

    def release(self, name):
        self._ensure_open()
        _validate_key(name)
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]
        return None

    def _has_conflict(self):
        watched_keys = self._read_keys | set(self._writes)
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in watched_keys:
                    return True
                if any(key.startswith(prefix) for prefix in self._read_prefixes):
                    return True
        return False

    def commit(self):
        self._ensure_open()

        if self._has_conflict():
            self._closed = True
            raise Conflict("transaction conflicts with a committed write")

        if not self._writes:
            self._closed = True
            return self._engine._version

        new_version = self._engine._version + 1
        ordered_keys = sorted(self._writes)
        new_data = self._engine._data.copy()
        record_writes = []
        for key in ordered_keys:
            value = self._writes[key]
            if value is _TOMBSTONE:
                new_data.pop(key, None)
                logged_value = None
            else:
                new_data[key] = value
                logged_value = value
            record_writes.append([key, logged_value])

        record = {"version": new_version, "writes": record_writes}
        self._engine._data = new_data
        self._engine._version = new_version
        self._engine._log.append(record)
        self._closed = True
        return new_version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._savepoints.clear()
        self._read_keys.clear()
        self._read_prefixes.clear()
        return None
