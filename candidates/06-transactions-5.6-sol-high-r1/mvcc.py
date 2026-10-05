"""A small in-memory serializable MVCC engine.

Transactions use copied snapshots for visibility.  The commit log is retained
both for recovery and for optimistic validation against every write performed
after a transaction's snapshot.
"""


class Conflict(RuntimeError):
    """Raised when a transaction cannot be serialized at commit time."""


def _valid_key(key):
    return isinstance(key, str) and len(key) > 0


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(version):
    return isinstance(version, int) and not isinstance(version, bool)


def _copy_record(record):
    """Copy a normalized internal log record for an API result."""
    return {
        "version": record["version"],
        "writes": [[key, value] for key, value in record["writes"]],
    }


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        elif isinstance(initial, dict):
            # Validate everything before installing any constructor state.
            for key, value in initial.items():
                if not _valid_key(key) or not _valid_value(value):
                    raise ValueError("initial data contains an invalid key or value")
            data = dict(initial)
        else:
            raise ValueError("initial must be a dict or None")

        self._data = data
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
        if (
            not _valid_version(version)
            or version < self._base_version
            or version > self._version
        ):
            raise ValueError("version is outside the retained log range")
        return [
            _copy_record(record)
            for record in self._log
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        """Validate and replay a checkpoint and its contiguous commit log."""
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")

        checkpoint_version = checkpoint["version"]
        checkpoint_data = checkpoint["data"]
        if not _valid_version(checkpoint_version) or checkpoint_version < 0:
            raise ValueError("invalid checkpoint version")
        if not isinstance(checkpoint_data, dict):
            raise ValueError("invalid checkpoint data")
        for key, value in checkpoint_data.items():
            if not _valid_key(key) or not _valid_value(value):
                raise ValueError("checkpoint data contains an invalid key or value")

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Normalize into fresh containers while validating the complete input.
        normalized_records = []
        expected_version = checkpoint_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid log record")
            record_version = record["version"]
            writes = record["writes"]
            if not _valid_version(record_version) or record_version != expected_version:
                raise ValueError("log versions must be contiguous")
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            normalized_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each write must be a two-element list")
                key, value = pair
                if not _valid_key(key):
                    raise ValueError("invalid write key")
                if value is not None and not _valid_value(value):
                    raise ValueError("invalid write value")
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                normalized_writes.append([key, value])
                previous_key = key

            normalized_records.append(
                {"version": record_version, "writes": normalized_writes}
            )
            expected_version += 1

        # No input can fail validation beyond this point. Build and replay only
        # from normalized copies so caller mutations cannot alias the engine.
        engine = cls(dict(checkpoint_data))
        engine._version = checkpoint_version
        engine._base_version = checkpoint_version
        engine._log = normalized_records
        for record in normalized_records:
            for key, value in record["writes"]:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
            engine._version = record["version"]
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

    def get(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._key_reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
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
        self._savepoints.append((name, dict(self._writes)))
        return None

    def rollback_to(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        index = self._savepoint_index(name)
        if index is None:
            raise ValueError("unknown savepoint")
        self._writes = dict(self._savepoints[index][1])
        self._savepoints = self._savepoints[: index + 1]
        return None

    def release(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("savepoint name must be a nonempty string")
        index = self._savepoint_index(name)
        if index is None:
            raise ValueError("unknown savepoint")
        self._savepoints = self._savepoints[:index]
        return None

    def _savepoint_index(self, name):
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        return None

    def commit(self):
        self._ensure_open()

        touched = set(self._key_reads)
        touched.update(self._writes)
        conflict = False
        for record in self._engine._log:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in touched or any(
                    key.startswith(prefix) for prefix in self._range_reads
                ):
                    conflict = True
                    break
            if conflict:
                break

        # Both successful and conflicting commit attempts consume the
        # transaction. Do this before raising Conflict.
        self._closed = True
        if conflict:
            self._discard_state()
            raise Conflict("transaction conflicts with a committed write")

        if self._writes:
            new_version = self._engine._version + 1
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._version = new_version
            self._engine._log.append({"version": new_version, "writes": writes})

        committed_version = self._engine._version
        self._discard_state()
        return committed_version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._discard_state()
        return None

    def _discard_state(self):
        self._snapshot = {}
        self._writes = {}
        self._key_reads = set()
        self._range_reads = set()
        self._savepoints = []
