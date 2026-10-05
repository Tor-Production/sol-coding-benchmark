"""A small in-memory, serializable MVCC engine."""


class Conflict(RuntimeError):
    """Raised when optimistic serializable validation fails."""


def _valid_key(value):
    return isinstance(value, str) and bool(value)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            data = {}
        elif isinstance(initial, dict):
            if any(not _valid_key(k) or not _valid_value(v)
                   for k, v in initial.items()):
                raise ValueError("invalid initial data")
            data = dict(initial)
        else:
            raise ValueError("initial must be a dict or None")
        self._data = data
        self._version = 0
        self._base_version = 0
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._version, dict(self._data))

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        if (not _valid_version(version) or
                version < self._base_version or version > self._version):
            raise ValueError("version is outside retained history")
        return [{"version": record["version"],
                 "writes": [[key, value] for key, value in record["writes"]]}
                for record in self._records if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        # Validate and copy the complete input before constructing any state.
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base = checkpoint.get("version")
        source_data = checkpoint.get("data")
        if not _valid_version(base) or not isinstance(source_data, dict):
            raise ValueError("invalid checkpoint")
        if any(not _valid_key(k) or not _valid_value(v)
               for k, v in source_data.items()):
            raise ValueError("invalid checkpoint data")
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        copied_records = []
        expected = base + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            version = record.get("version")
            writes = record.get("writes")
            if (not _valid_version(version) or version != expected or
                    not isinstance(writes, list) or not writes):
                raise ValueError("invalid record")
            copied_writes = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid write")
                key, value = pair
                if (not _valid_key(key) or
                        (value is not None and not _valid_value(value)) or
                        (previous is not None and key <= previous)):
                    raise ValueError("invalid write")
                copied_writes.append([key, value])
                previous = key
            copied_records.append({"version": version, "writes": copied_writes})
            expected += 1

        data = dict(source_data)
        for record in copied_records:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
        engine = cls()
        engine._data = data
        engine._version = expected - 1
        engine._base_version = base
        engine._records = copied_records
        return engine


class _Transaction:
    def __init__(self, engine, version, snapshot):
        self._engine = engine
        self._snapshot_version = version
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
        if not _valid_key(key):
            raise ValueError("invalid key")
        self._read_keys.add(key)
        return self._writes[key] if key in self._writes else self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("invalid prefix")
        self._read_prefixes.add(prefix)
        visible = dict(self._snapshot)
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible) if key.startswith(prefix)}

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
            raise ValueError("duplicate savepoint")
        self._savepoints.append((name, dict(self._writes)))

    def _savepoint_index(self, name):
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("invalid savepoint name")
        index = self._savepoint_index(name)
        self._writes = dict(self._savepoints[index][1])
        self._savepoints = self._savepoints[:index + 1]

    def release(self, name):
        self._ensure_open()
        if not _valid_key(name):
            raise ValueError("invalid savepoint name")
        index = self._savepoint_index(name)
        self._savepoints = self._savepoints[:index]

    def commit(self):
        self._ensure_open()
        self._closed = True
        relevant_keys = self._read_keys | set(self._writes)
        for record in self._engine._records:
            if record["version"] <= self._snapshot_version:
                continue
            for key, _ in record["writes"]:
                if (key in relevant_keys or
                        any(key.startswith(prefix) for prefix in self._read_prefixes)):
                    self._discard()
                    raise Conflict("transaction conflicts with committed history")
        if self._writes:
            version = self._engine._version + 1
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._version = version
            self._engine._records.append({"version": version, "writes": writes})
        result = self._engine._version
        self._discard()
        return result

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._discard()

    def _discard(self):
        self._snapshot.clear()
        self._writes.clear()
        self._read_keys.clear()
        self._read_prefixes.clear()
        self._savepoints.clear()
