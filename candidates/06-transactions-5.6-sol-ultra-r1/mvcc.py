"""A small in-memory MVCC engine with serializable validation.

Transactions read from a copy of the committed state taken at ``begin`` and
overlay their own writes on that copy.  The engine keeps every retained commit
record so validation observes writes that have since been overwritten or
deleted, rather than merely comparing current values.
"""


class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""


def _is_integer(value):
    """Return whether *value* is an allowed stored/version integer."""

    return isinstance(value, int) and not isinstance(value, bool)


def _valid_key(value):
    return isinstance(value, str) and bool(value)


def _copy_valid_data(value):
    """Validate a data dictionary and return a detached, sorted copy."""

    if not isinstance(value, dict):
        raise ValueError("data must be a dict")

    copied = {}
    for key, item in value.items():
        if not _valid_key(key):
            raise ValueError("keys must be nonempty strings")
        if not _is_integer(item):
            raise ValueError("stored values must be integers")
        copied[key] = item

    # Keeping internal data ordered is not required for correctness, but makes
    # every externally copied representation deterministic as well.
    return {key: copied[key] for key in sorted(copied)}


def _require_key(key):
    if not _valid_key(key):
        raise ValueError("key must be a nonempty string")


def _require_name(name):
    if not _valid_key(name):
        raise ValueError("savepoint name must be a nonempty string")


def _copy_record(record):
    """Return a defensive public copy of an internal commit record."""

    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        else:
            data = _copy_valid_data(initial)

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
        if not _is_integer(version):
            raise ValueError("version must be an integer")
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside retained history")

        return [
            _copy_record(record)
            for record in self._log
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        """Validate and replay a checkpoint and its contiguous commit log."""

        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be a dict")
        if set(checkpoint) != {"version", "data"}:
            raise ValueError("checkpoint must contain exactly version and data")

        base_version = checkpoint["version"]
        if not _is_integer(base_version) or base_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        checkpoint_data = _copy_valid_data(checkpoint["data"])

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Normalize the complete log before replaying any of it.  Besides
        # making restore all-or-nothing, this detaches all nested input lists.
        normalized = []
        expected_version = base_version + 1
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("each record must be a dict")
            if set(record) != {"version", "writes"}:
                raise ValueError("record must contain exactly version and writes")

            record_version = record["version"]
            if not _is_integer(record_version) or record_version != expected_version:
                raise ValueError("record versions must be contiguous")

            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            copied_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("writes must contain two-item lists")
                key, value = pair
                if not _valid_key(key):
                    raise ValueError("write keys must be nonempty strings")
                if value is not None and not _is_integer(value):
                    raise ValueError("write values must be integers or None")
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                copied_writes.append([key, value])
                previous_key = key

            normalized.append(
                {"version": record_version, "writes": copied_writes}
            )
            expected_version += 1

        replayed = checkpoint_data.copy()
        for record in normalized:
            for key, value in record["writes"]:
                if value is None:
                    replayed.pop(key, None)
                else:
                    replayed[key] = value

        engine = cls(replayed)
        engine._version = expected_version - 1
        engine._base_version = base_version
        engine._log = [_copy_record(record) for record in normalized]
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
        _require_key(key)

        self._read_keys.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")

        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value

        result = {
            key: visible[key]
            for key in sorted(visible)
            if key.startswith(prefix)
        }
        self._scan_prefixes.add(prefix)
        return result

    def put(self, key, value):
        self._ensure_open()
        _require_key(key)
        if not _is_integer(value):
            raise ValueError("value must be an integer")
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _require_key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        _require_name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        _require_name(name)
        index = self._savepoint_index(name)

        self._writes = self._savepoints[index][1].copy()
        self._savepoints = self._savepoints[: index + 1]

    def release(self, name):
        self._ensure_open()
        _require_name(name)
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]

    def _conflicts(self):
        watched_keys = self._read_keys | set(self._writes)
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in watched_keys:
                    return True
                if any(key.startswith(prefix) for prefix in self._scan_prefixes):
                    return True
        return False

    def _close(self):
        self._closed = True
        self._snapshot = {}
        self._writes = {}
        self._read_keys = set()
        self._scan_prefixes = set()
        self._savepoints = []

    def commit(self):
        self._ensure_open()

        if self._conflicts():
            self._close()
            raise Conflict("transaction conflicts with a committed write")

        writes = [[key, self._writes[key]] for key in sorted(self._writes)]
        if writes:
            new_data = self._engine._data.copy()
            for key, value in writes:
                if value is None:
                    new_data.pop(key, None)
                else:
                    new_data[key] = value

            new_version = self._engine._version + 1
            record = {"version": new_version, "writes": writes}

            self._engine._data = new_data
            self._engine._version = new_version
            self._engine._log.append(record)

        committed_version = self._engine._version
        self._close()
        return committed_version

    def abort(self):
        self._ensure_open()
        self._close()
