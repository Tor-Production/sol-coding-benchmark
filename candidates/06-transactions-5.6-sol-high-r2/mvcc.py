"""A small in-memory serializable MVCC engine.

Transactions read from a private snapshot and are validated against the
engine's committed write history when they commit.  The history is also the
logical recovery log exported by :meth:`Engine.log_since`.
"""


class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""


def _valid_name(value):
    return isinstance(value, str) and bool(value)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _copy_record(record):
    """Return the public, mutable representation of an internal record."""
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
            if not isinstance(initial, dict):
                raise ValueError("initial must be a dict or None")
            data = {}
            for key, value in initial.items():
                if not _valid_name(key) or not _valid_value(value):
                    raise ValueError("invalid initial key or value")
                data[key] = value

        self._data = data
        self._version = 0
        self._base_version = 0
        self._records = []

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
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < self._base_version
            or version > self._version
        ):
            raise ValueError("version is outside retained history")

        return [
            _copy_record(record)
            for record in self._records
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        """Validate and replay a checkpoint and its following commit log."""
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "version",
            "data",
        }:
            raise ValueError("invalid checkpoint")

        base_version = checkpoint["version"]
        checkpoint_data = checkpoint["data"]
        if (
            not isinstance(base_version, int)
            or isinstance(base_version, bool)
            or base_version < 0
            or not isinstance(checkpoint_data, dict)
        ):
            raise ValueError("invalid checkpoint")

        data = {}
        for key, value in checkpoint_data.items():
            if not _valid_name(key) or not _valid_value(value):
                raise ValueError("invalid checkpoint data")
            data[key] = value

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and copy the entire log before replaying any of it.
        copied_records = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {
                "version",
                "writes",
            }:
                raise ValueError("invalid log record")

            record_version = record["version"]
            writes = record["writes"]
            if (
                not isinstance(record_version, int)
                or isinstance(record_version, bool)
                or record_version != expected_version
                or not isinstance(writes, list)
                or not writes
            ):
                raise ValueError("invalid log record")

            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid logged write")
                key, value = pair
                if not _valid_name(key):
                    raise ValueError("invalid logged key")
                if previous_key is not None and key <= previous_key:
                    raise ValueError("logged keys must be unique and sorted")
                if value is not None and not _valid_value(value):
                    raise ValueError("invalid logged value")
                copied_writes.append([key, value])
                previous_key = key

            copied_records.append(
                {"version": record_version, "writes": copied_writes}
            )
            expected_version += 1

        # Replay only after every input object has passed validation.
        for record in copied_records:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls(data)
        engine._base_version = base_version
        engine._version = expected_version - 1
        engine._records = copied_records
        return engine


class _Transaction:
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
        if not _valid_name(key):
            raise ValueError("key must be a nonempty string")

        self._read_keys.add(key)
        if key in self._writes:
            return self._writes[key]
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
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)}

    def put(self, key, value):
        self._ensure_open()
        if not _valid_name(key) or not _valid_value(value):
            raise ValueError("invalid key or value")
        self._writes[key] = value
        return None

    def delete(self, key):
        self._ensure_open()
        if not _valid_name(key):
            raise ValueError("key must be a nonempty string")
        self._writes[key] = None
        return None

    def savepoint(self, name):
        self._ensure_open()
        if not _valid_name(name):
            raise ValueError("savepoint name must be a nonempty string")
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, self._writes.copy()))
        return None

    def _savepoint_index(self, name):
        if not _valid_name(name):
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
        write_keys = self._writes.keys()
        for record in self._engine._records:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in self._read_keys or key in write_keys:
                    return True
                if any(key.startswith(prefix) for prefix in self._read_prefixes):
                    return True
        return False

    def commit(self):
        self._ensure_open()

        if self._has_conflict():
            self._closed = True
            raise Conflict("transaction conflicts with a committed write")

        if self._writes:
            next_version = self._engine._version + 1
            ordered_writes = [
                [key, self._writes[key]] for key in sorted(self._writes)
            ]

            # The staged writes have already been validated by put/delete, so
            # applying them cannot fail partway through.
            for key, value in ordered_writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._version = next_version
            self._engine._records.append(
                {"version": next_version, "writes": ordered_writes}
            )

        self._closed = True
        return self._engine._version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._read_keys.clear()
        self._read_prefixes.clear()
        self._savepoints.clear()
        return None


__all__ = ["Engine", "Conflict"]
