class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""

    pass


def _valid_string(value):
    return isinstance(value, str) and bool(value)


def _valid_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_key(key):
    if not _valid_string(key):
        raise ValueError("keys must be nonempty strings")


def _validate_value(value):
    if not _valid_integer(value):
        raise ValueError("values must be integers (but not bool)")


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        else:
            if not isinstance(initial, dict):
                raise ValueError("initial must be a dict or None")

            # Validate everything before publishing any state on the instance.
            items = list(initial.items())
            for key, value in items:
                _validate_key(key)
                _validate_value(value)
            data = dict(items)

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
        if not _valid_integer(version):
            raise ValueError("version must be an integer (but not bool)")
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside retained history")

        return [
            {
                "version": record["version"],
                "writes": [[key, value] for key, value in record["writes"]],
            }
            for record in self._records
            if record["version"] > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        # Build validated private copies first.  No input container is retained,
        # and replay does not begin until the entire recovery stream is valid.
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be a dict")
        if set(checkpoint) != {"version", "data"}:
            raise ValueError("checkpoint must contain exactly version and data")

        checkpoint_version = checkpoint["version"]
        if not _valid_integer(checkpoint_version) or checkpoint_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")

        checkpoint_data = checkpoint["data"]
        if not isinstance(checkpoint_data, dict):
            raise ValueError("checkpoint data must be a dict")
        checkpoint_items = list(checkpoint_data.items())
        for key, value in checkpoint_items:
            _validate_key(key)
            _validate_value(value)

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        validated_records = []
        expected_version = checkpoint_version + 1
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("each record must be a dict")
            if set(record) != {"version", "writes"}:
                raise ValueError("records must contain exactly version and writes")

            record_version = record["version"]
            if not _valid_integer(record_version):
                raise ValueError("record versions must be integers")
            if record_version != expected_version:
                raise ValueError("record versions must be contiguous")

            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")

            validated_writes = []
            previous_key = None
            for write in writes:
                if not isinstance(write, list) or len(write) != 2:
                    raise ValueError("each write must be a two-item list")
                key, value = write
                _validate_key(key)
                if value is not None:
                    _validate_value(value)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and strictly sorted")
                previous_key = key
                validated_writes.append([key, value])

            validated_records.append(
                {"version": record_version, "writes": validated_writes}
            )
            expected_version += 1

        data = dict(checkpoint_items)
        for record in validated_records:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls()
        engine._data = data
        engine._version = expected_version - 1
        engine._base_version = checkpoint_version
        engine._records = validated_records
        return engine


class _Transaction:
    """A transaction over an immutable-at-begin snapshot."""

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

    def _discard_and_close(self):
        self._closed = True
        self._snapshot = {}
        self._writes = {}
        self._key_reads = set()
        self._range_reads = set()
        self._savepoints = []

    def get(self, key):
        self._ensure_open()
        _validate_key(key)

        self._key_reads.add(key)
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
        self._range_reads.add(prefix)
        return result

    def put(self, key, value):
        self._ensure_open()
        _validate_key(key)
        _validate_value(value)
        self._writes[key] = value
        return None

    def delete(self, key):
        self._ensure_open()
        _validate_key(key)
        self._writes[key] = None
        return None

    def savepoint(self, name):
        self._ensure_open()
        _validate_key(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint names must be unique")
        self._savepoints.append((name, self._writes.copy()))
        return None

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
        self._savepoints = self._savepoints[: index + 1]
        return None

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]
        return None

    def commit(self):
        self._ensure_open()

        watched_keys = self._key_reads | self._writes.keys()
        conflict = False
        for record in self._engine._records:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if key in watched_keys or any(
                    key.startswith(prefix) for prefix in self._range_reads
                ):
                    conflict = True
                    break
            if conflict:
                break

        if conflict:
            self._discard_and_close()
            raise Conflict("transaction conflicts with a committed write")

        if not self._writes:
            version = self._engine._version
            self._discard_and_close()
            return version

        writes = [[key, self._writes[key]] for key in sorted(self._writes)]
        data = self._engine._data.copy()
        for key, value in writes:
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value

        version = self._engine._version + 1
        record = {"version": version, "writes": writes}
        self._engine._data = data
        self._engine._version = version
        self._engine._records.append(record)
        self._discard_and_close()
        return version

    def abort(self):
        self._ensure_open()
        self._discard_and_close()
        return None
