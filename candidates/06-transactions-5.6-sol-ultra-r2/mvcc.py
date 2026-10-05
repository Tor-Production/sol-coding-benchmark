"""A small in-memory MVCC engine with serializable validation.

Transactions read from a private snapshot and keep their writes locally.  At
commit time, the engine compares every intervening committed write with the
transaction's point reads, range reads, and final write set.
"""


class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""


def _valid_string(value, *, allow_empty):
    return isinstance(value, str) and (allow_empty or bool(value))


def _valid_value(value):
    # bool is an int subclass, but is not a stored integer for this API.
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(value):
    return _valid_value(value) and value >= 0


def _copy_record(record):
    """Return the public, fully detached representation of a log record."""
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        if not isinstance(initial, dict):
            raise ValueError("initial must be a dict or None")

        # Validate everything before publishing any engine state.
        copied = {}
        for key, value in initial.items():
            if not _valid_string(key, allow_empty=False):
                raise ValueError("keys must be nonempty strings")
            if not _valid_value(value):
                raise ValueError("values must be integers (but not bool)")
            copied[key] = value

        self._data = copied
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, self._data.copy())

    def checkpoint(self):
        data = {key: self._data[key] for key in sorted(self._data)}
        return {"version": self._version, "data": data}

    def log_since(self, version):
        if not _valid_version(version):
            raise ValueError("version must be a nonnegative integer")
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside the retained log range")

        return [
            _copy_record(record)
            for record in self._log
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        """Validate and replay a checkpoint plus its contiguous commit log."""
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be a dict")
        if set(checkpoint) != {"version", "data"}:
            raise ValueError("checkpoint must contain exactly version and data")

        checkpoint_version = checkpoint["version"]
        checkpoint_data = checkpoint["data"]
        if not _valid_version(checkpoint_version):
            raise ValueError("checkpoint version must be nonnegative")
        if not isinstance(checkpoint_data, dict):
            raise ValueError("checkpoint data must be a dict")

        base_data = {}
        for key, value in checkpoint_data.items():
            if not _valid_string(key, allow_empty=False):
                raise ValueError("checkpoint keys must be nonempty strings")
            if not _valid_value(value):
                raise ValueError("checkpoint values must be integers")
            base_data[key] = value

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Build a normalized, detached log while validating the whole input.
        normalized_records = []
        expected_version = checkpoint_version + 1
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("each record must be a dict")
            if set(record) != {"version", "writes"}:
                raise ValueError("records must contain exactly version and writes")

            record_version = record["version"]
            writes = record["writes"]
            if (
                not _valid_version(record_version)
                or record_version != expected_version
            ):
                raise ValueError("record versions must be contiguous")
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            normalized_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a two-item list")
                key, value = pair
                if not _valid_string(key, allow_empty=False):
                    raise ValueError("write keys must be nonempty strings")
                if value is not None and not _valid_value(value):
                    raise ValueError("write values must be integers or None")
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and sorted")
                normalized_writes.append([key, value])
                previous_key = key

            normalized_records.append(
                {"version": record_version, "writes": normalized_writes}
            )
            expected_version += 1

        # No stateful object is created until every checkpoint and record field
        # above has passed validation.
        engine = cls(base_data)
        replayed = base_data.copy()
        for record in normalized_records:
            for key, value in record["writes"]:
                if value is None:
                    replayed.pop(key, None)
                else:
                    replayed[key] = value

        engine._data = replayed
        engine._version = expected_version - 1
        engine._base_version = checkpoint_version
        engine._log = [_copy_record(record) for record in normalized_records]
        return engine


class _Transaction:
    def __init__(self, engine, snapshot_version, snapshot):
        self._engine = engine
        self._snapshot_version = snapshot_version
        self._snapshot = snapshot
        self._writes = {}
        self._key_reads = set()
        self._range_reads = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    @staticmethod
    def _check_key(key):
        if not _valid_string(key, allow_empty=False):
            raise ValueError("key must be a nonempty string")

    @staticmethod
    def _check_savepoint_name(name):
        if not _valid_string(name, allow_empty=False):
            raise ValueError("savepoint name must be a nonempty string")

    def get(self, key):
        self._ensure_open()
        self._check_key(key)
        self._key_reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not _valid_string(prefix, allow_empty=True):
            raise ValueError("prefix must be a string")

        self._range_reads.add(prefix)
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
        return {key: visible[key] for key in sorted(visible)}

    def put(self, key, value):
        self._ensure_open()
        self._check_key(key)
        if not _valid_value(value):
            raise ValueError("value must be an integer (but not bool)")
        self._writes[key] = value
        return None

    def delete(self, key):
        self._ensure_open()
        self._check_key(key)
        self._writes[key] = None
        return None

    def savepoint(self, name):
        self._ensure_open()
        self._check_savepoint_name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))
        return None

    def _savepoint_index(self, name):
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        return None

    def rollback_to(self, name):
        self._ensure_open()
        self._check_savepoint_name(name)
        index = self._savepoint_index(name)
        if index is None:
            raise ValueError("unknown savepoint")

        restored_writes = self._savepoints[index][1].copy()
        self._writes = restored_writes
        self._savepoints = self._savepoints[: index + 1]
        return None

    def release(self, name):
        self._ensure_open()
        self._check_savepoint_name(name)
        index = self._savepoint_index(name)
        if index is None:
            raise ValueError("unknown savepoint")

        self._savepoints = self._savepoints[:index]
        return None

    def _conflicts(self):
        watched_keys = self._key_reads | set(self._writes)
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in watched_keys:
                    return True
                if any(key.startswith(prefix) for prefix in self._range_reads):
                    return True
        return False

    def _close(self):
        self._closed = True
        self._snapshot = {}
        self._writes = {}
        self._key_reads.clear()
        self._range_reads.clear()
        self._savepoints = []

    def commit(self):
        self._ensure_open()
        if self._conflicts():
            self._close()
            raise Conflict("transaction conflicts with a committed write")

        writes = self._writes
        if writes:
            new_version = self._engine._version + 1
            ordered_writes = [[key, writes[key]] for key in sorted(writes)]

            new_data = self._engine._data.copy()
            for key, value in ordered_writes:
                if value is None:
                    new_data.pop(key, None)
                else:
                    new_data[key] = value

            record = {"version": new_version, "writes": ordered_writes}
            self._engine._data = new_data
            self._engine._log.append(record)
            self._engine._version = new_version

        committed_version = self._engine._version
        self._close()
        return committed_version

    def abort(self):
        self._ensure_open()
        self._close()
        return None
