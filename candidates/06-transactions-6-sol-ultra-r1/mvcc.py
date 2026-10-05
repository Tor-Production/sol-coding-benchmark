"""An in-memory, snapshot-based store with serializable commit validation."""


class Conflict(RuntimeError):
    """A transaction observed or wrote a key changed after its snapshot."""


def _valid_key(key):
    return isinstance(key, str) and bool(key)


def _valid_value(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_version(version):
    return isinstance(version, int) and not isinstance(version, bool)


def _copy_data(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    copied = {}
    for key, value in data.items():
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("data must have nonempty string keys and integer values")
        copied[key] = value
    return copied


def _copy_record(record, expected_version):
    if not isinstance(record, dict) or set(record) != {"version", "writes"}:
        raise ValueError("record must contain exactly version and writes")
    version = record["version"]
    if not _valid_version(version) or version != expected_version:
        raise ValueError("record versions must be contiguous")
    writes = record["writes"]
    if not isinstance(writes, list) or not writes:
        raise ValueError("record writes must be a nonempty list")

    copied_writes = []
    previous_key = None
    for pair in writes:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("each write must be a two-element list")
        key, value = pair
        if not _valid_key(key) or (value is not None and not _valid_value(value)):
            raise ValueError("invalid write")
        if previous_key is not None and key <= previous_key:
            raise ValueError("write keys must be unique and strictly sorted")
        copied_writes.append([key, value])
        previous_key = key
    return {"version": version, "writes": copied_writes}


class Engine:
    def __init__(self, initial=None):
        data = {} if initial is None else _copy_data(initial)
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
        return {"version": self._version, "data": dict(sorted(self._data.items()))}

    def log_since(self, version):
        if (not _valid_version(version)
                or version < self._base_version
                or version > self._version):
            raise ValueError("version is outside the retained history")
        return [
            {"version": record["version"],
             "writes": [pair.copy() for pair in record["writes"]]}
            for record in self._records[version - self._base_version:]
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) != {"version", "data"}):
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        if not _valid_version(base_version) or base_version < 0:
            raise ValueError("checkpoint version must be a nonnegative integer")
        data = _copy_data(checkpoint["data"])
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        # Validate and copy the entire log before replaying any records.
        copied_records = [
            _copy_record(record, base_version + index)
            for index, record in enumerate(records, start=1)
        ]
        for record in copied_records:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls(data)
        engine._base_version = base_version
        engine._version = base_version + len(copied_records)
        engine._records = copied_records
        return engine


class _Transaction:
    def __init__(self, engine, snapshot_version, snapshot):
        self._engine = engine
        self._snapshot_version = snapshot_version
        self._snapshot = snapshot
        self._writes = {}
        self._reads = set()
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
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._scan_prefixes.add(prefix)
        visible = {key: value for key, value in self._snapshot.items()
                   if key.startswith(prefix)}
        for key, value in self._writes.items():
            if key.startswith(prefix):
                if value is None:
                    visible.pop(key, None)
                else:
                    visible[key] = value
        return dict(sorted(visible.items()))

    def put(self, key, value):
        self._ensure_open()
        if not _valid_key(key) or not _valid_value(value):
            raise ValueError("put requires a nonempty string key and integer value")
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        if not _valid_key(key):
            raise ValueError("key must be a nonempty string")
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        if not _valid_key(name) or any(saved_name == name
                                       for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name must be unique and nonempty")
        self._savepoints.append((name, self._writes.copy()))

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
        del self._savepoints[index + 1:]

    def release(self, name):
        self._ensure_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._ensure_open()
        engine = self._engine
        watched_keys = self._reads | self._writes.keys()
        for record in engine._records[
                self._snapshot_version - engine._base_version:]:
            for key, _ in record["writes"]:
                if (key in watched_keys
                        or any(key.startswith(prefix)
                               for prefix in self._scan_prefixes)):
                    self._closed = True
                    raise Conflict("a committed write conflicts with this transaction")

        if self._writes:
            new_data = engine._data.copy()
            sorted_writes = []
            for key, value in sorted(self._writes.items()):
                sorted_writes.append([key, value])
                if value is None:
                    new_data.pop(key, None)
                else:
                    new_data[key] = value
            next_version = engine._version + 1
            engine._records.append({"version": next_version,
                                    "writes": sorted_writes})
            engine._data = new_data
            engine._version = next_version

        self._closed = True
        return engine._version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._savepoints.clear()
        self._reads.clear()
        self._scan_prefixes.clear()
