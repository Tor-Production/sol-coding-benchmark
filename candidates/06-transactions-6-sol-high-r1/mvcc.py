"""An in-memory MVCC store with optimistic serializable commits."""


class Conflict(RuntimeError):
    """A transaction's reads or writes overlap a later committed write."""


def _key(value):
    if not isinstance(value, str) or not value:
        raise ValueError("key must be a nonempty string")


def _name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("savepoint name must be a nonempty string")


def _value(value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("stored value must be an integer")


def _version(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("version must be a nonnegative integer")


class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        if not isinstance(initial, dict):
            raise ValueError("initial data must be a dict")
        data = {}
        for key, value in initial.items():
            _key(key)
            _value(value)
            data[key] = value
        self._data = data
        self._version = 0
        self._base_version = 0
        self._records = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._data.copy(), self._version)

    def checkpoint(self):
        return {"version": self._version,
                "data": {key: self._data[key] for key in sorted(self._data)}}

    def log_since(self, version):
        _version(version)
        if version < self._base_version or version > self._version:
            raise ValueError("version is outside retained history")
        return [{"version": record["version"],
                 "writes": [pair.copy() for pair in record["writes"]]}
                for record in self._records
                if record["version"] > version]

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"version", "data"}:
            raise ValueError("invalid checkpoint")
        base = checkpoint["version"]
        _version(base)
        source = checkpoint["data"]
        if not isinstance(source, dict):
            raise ValueError("checkpoint data must be a dict")
        data = {}
        for key, value in source.items():
            _key(key)
            _value(value)
            data[key] = value
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        retained = []
        expected = base + 1
        for record in records:
            if not isinstance(record, dict) or set(record) != {"version", "writes"}:
                raise ValueError("invalid record")
            record_version = record["version"]
            _version(record_version)
            if record_version != expected:
                raise ValueError("record versions must be contiguous")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("writes must be a nonempty list")
            validated = []
            previous = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a two-element list")
                key, value = pair
                _key(key)
                if previous is not None and key <= previous:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    _value(value)
                validated.append([key, value])
                previous = key
            for key, value in validated:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            retained.append({"version": record_version, "writes": validated})
            expected += 1

        engine = cls(data)
        engine._version = expected - 1
        engine._base_version = base
        engine._records = retained
        return engine


class _Transaction:
    def __init__(self, engine, snapshot, version):
        self._engine = engine
        self._snapshot = snapshot
        self._version = version
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
        _key(key)
        self._read_keys.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._ensure_open()
        if not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        self._read_prefixes.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._ensure_open()
        _key(key)
        _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._ensure_open()
        _key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._ensure_open()
        _name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("duplicate savepoint name")
        self._savepoints.append((name, self._writes.copy()))

    def rollback_to(self, name):
        self._ensure_open()
        _name(name)
        for index, (saved_name, writes) in enumerate(self._savepoints):
            if saved_name == name:
                self._writes = writes.copy()
                self._savepoints = self._savepoints[:index + 1]
                return
        raise ValueError("unknown savepoint")

    def release(self, name):
        self._ensure_open()
        _name(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                self._savepoints = self._savepoints[:index]
                return
        raise ValueError("unknown savepoint")

    def commit(self):
        self._ensure_open()
        engine = self._engine
        self._closed = True
        watched = self._read_keys | self._writes.keys()
        for record in engine._records:
            if record["version"] <= self._version:
                continue
            for key, _ in record["writes"]:
                if key in watched or any(key.startswith(prefix)
                                         for prefix in self._read_prefixes):
                    raise Conflict("transaction conflicts with a committed write")

        if self._writes:
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    engine._data.pop(key, None)
                else:
                    engine._data[key] = value
            engine._version += 1
            engine._records.append({"version": engine._version, "writes": writes})
        return engine._version

    def abort(self):
        self._ensure_open()
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._read_keys.clear()
        self._read_prefixes.clear()
        self._savepoints.clear()
