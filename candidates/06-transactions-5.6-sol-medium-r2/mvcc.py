"""A small, in-memory serializable MVCC store."""


class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""


def _valid_key(key):
    return isinstance(key, str) and bool(key)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(version):
    return _valid_value(version) and version >= 0


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        elif isinstance(initial, dict):
            data = {}
            for key, value in initial.items():
                if not _valid_key(key) or not _valid_value(value):
                    raise ValueError("invalid initial data")
                data[key] = value
        else:
            raise ValueError("initial must be None or a dict")

        self._data = data
        self._version = 0
        self._base_version = 0
        # Internally records are (version, ((key, value_or_None), ...)).
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
        if (not _valid_version(version) or
                version < self._base_version or version > self._version):
            raise ValueError("version is outside retained history")
        return [
            {
                "version": record_version,
                "writes": [[key, value] for key, value in writes],
            }
            for record_version, writes in self._log
            if record_version > version
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        # Build only local, detached values until the entire input has passed
        # validation. In particular, no partially replayed Engine is exposed.
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        checkpoint_version = checkpoint["version"]
        checkpoint_data = checkpoint["data"]
        if not _valid_version(checkpoint_version) or not isinstance(checkpoint_data, dict):
            raise ValueError("invalid checkpoint")

        data = {}
        for key, value in checkpoint_data.items():
            if not _valid_key(key) or not _valid_value(value):
                raise ValueError("invalid checkpoint data")
            data[key] = value

        if not isinstance(records, list):
            raise ValueError("records must be a list")

        validated_records = []
        expected_version = checkpoint_version + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            record_version = record["version"]
            writes = record["writes"]
            if (not _valid_version(record_version) or
                    record_version != expected_version or
                    not isinstance(writes, list) or not writes):
                raise ValueError("invalid record")

            validated_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid write")
                key, value = pair
                if (not _valid_key(key) or
                        (value is not None and not _valid_value(value)) or
                        (previous_key is not None and key <= previous_key)):
                    raise ValueError("invalid write")
                validated_writes.append((key, value))
                previous_key = key

            validated_records.append((record_version, tuple(validated_writes)))
            expected_version += 1

        replayed = data.copy()
        for _, writes in validated_records:
            for key, value in writes:
                if value is None:
                    replayed.pop(key, None)
                else:
                    replayed[key] = value

        engine = cls(data)
        engine._data = replayed
        engine._version = expected_version - 1
        engine._base_version = checkpoint_version
        engine._log = validated_records
        return engine


class _Transaction:
    def __init__(self, engine, snapshot_version, snapshot):
        self._engine = engine
        self._snapshot_version = snapshot_version
        self._snapshot = snapshot
        self._writes = {}
        self._reads = set()
        self._prefixes = set()
        self._savepoints = []
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("invalid key")
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("invalid prefix")
        self._prefixes.add(prefix)
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

    def delete(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("invalid key")
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("invalid savepoint name")
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint name")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        if not _valid_key(name):
            raise ValueError("invalid savepoint name")
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._ensure_open()

        conflict = False
        for record_version, writes in self._engine._log:
            if record_version <= self._snapshot_version:
                continue
            for key, _ in writes:
                if (key in self._reads or key in self._writes or
                        any(key.startswith(prefix) for prefix in self._prefixes)):
                    conflict = True
                    break
            if conflict:
                break

        if conflict:
            self._close()
            raise Conflict("transaction conflicts with a committed write")

        if self._writes:
            new_version = self._engine._version + 1
            writes = tuple((key, self._writes[key]) for key in sorted(self._writes))
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._log.append((new_version, writes))
            self._engine._version = new_version

        result = self._engine._version
        self._close()
        return result

    def abort(self):
        self._ensure_open()
        self._close()

    def _close(self):
        self._closed = True
        self._snapshot = {}
        self._writes = {}
        self._reads = set()
        self._prefixes = set()
        self._savepoints = []
