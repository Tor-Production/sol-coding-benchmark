"""An in-memory MVCC store with optimistic serializable transactions."""


class Conflict(RuntimeError):
    """A transaction's observations or writes overlap a later commit."""


def _key(value):
    if not isinstance(value, str):
        raise ValueError("key must be a nonempty string")
    key = str.__str__(value)
    if not key:
        raise ValueError("key must be a nonempty string")
    return key


def _name(value):
    if not isinstance(value, str):
        raise ValueError("savepoint name must be a nonempty string")
    name = str.__str__(value)
    if not name:
        raise ValueError("savepoint name must be a nonempty string")
    return name


def _prefix(value):
    if not isinstance(value, str):
        raise ValueError("prefix must be a string")
    return str.__str__(value)


def _value(value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("stored value must be an integer other than bool")
    return int.__int__(value)


def _data_copy(data):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    result = {}
    for key, value in data.items():
        result[_key(key)] = _value(value)
    return result


class Engine:
    def __init__(self, initial=None):
        self._data = {} if initial is None else _data_copy(initial)
        self._version = 0
        self._base_version = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return _Transaction(self, self._data.copy(), self._version)

    def checkpoint(self):
        return {
            "version": self._version,
            "data": {key: self._data[key] for key in sorted(self._data)},
        }

    def log_since(self, version):
        if (not isinstance(version, int) or isinstance(version, bool)
                or not self._base_version <= version <= self._version):
            raise ValueError("version is outside retained history")
        return [
            {"version": record["version"],
             "writes": [pair.copy() for pair in record["writes"]]}
            for record in self._log[version - self._base_version:]
        ]

    @classmethod
    def restore(cls, checkpoint, records):
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) != {"version", "data"}):
            raise ValueError("checkpoint must contain exactly version and data")
        base_version = checkpoint["version"]
        if (not isinstance(base_version, int) or isinstance(base_version, bool)
                or base_version < 0):
            raise ValueError("checkpoint version must be nonnegative integer")
        data = _data_copy(checkpoint["data"])

        if not isinstance(records, list):
            raise ValueError("records must be a list")
        validated_records = []
        for expected_version, record in enumerate(records, base_version + 1):
            if (not isinstance(record, dict)
                    or set(record) != {"version", "writes"}
                    or not isinstance(record["version"], int)
                    or isinstance(record["version"], bool)
                    or record["version"] != expected_version):
                raise ValueError("record has invalid fields or version")
            writes = record["writes"]
            if not isinstance(writes, list) or not writes:
                raise ValueError("record writes must be a nonempty list")
            validated_writes = []
            previous_key = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("write must be a [key, value] list")
                key, value = pair
                key = _key(key)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("write keys must be unique and sorted")
                if value is not None:
                    value = _value(value)
                validated_writes.append([key, value])
                previous_key = key
            validated_records.append(
                {"version": expected_version, "writes": validated_writes}
            )

        for record in validated_records:
            for key, value in record["writes"]:
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value

        engine = cls(data)
        engine._base_version = base_version
        engine._version = base_version + len(validated_records)
        engine._log = validated_records
        return engine


class _Transaction:
    def __init__(self, engine, snapshot, snapshot_version):
        self._engine = engine
        self._snapshot = snapshot
        self._snapshot_version = snapshot_version
        self._writes = {}
        self._reads = set()
        self._prefixes = set()
        self._savepoints = []
        self._closed = False

    def _require_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def _close(self):
        self._closed = True
        self._snapshot.clear()
        self._writes.clear()
        self._reads.clear()
        self._prefixes.clear()
        self._savepoints.clear()

    def get(self, key):
        self._require_open()
        key = _key(key)
        self._reads.add(key)
        if key in self._writes:
            return self._writes[key]
        return self._snapshot.get(key)

    def scan(self, prefix=""):
        self._require_open()
        prefix = _prefix(prefix)
        self._prefixes.add(prefix)
        visible = self._snapshot.copy()
        for key, value in self._writes.items():
            if value is None:
                visible.pop(key, None)
            else:
                visible[key] = value
        return {key: visible[key] for key in sorted(visible)
                if key.startswith(prefix)}

    def put(self, key, value):
        self._require_open()
        key = _key(key)
        value = _value(value)
        self._writes[key] = value

    def delete(self, key):
        self._require_open()
        key = _key(key)
        self._writes[key] = None

    def savepoint(self, name):
        self._require_open()
        name = _name(name)
        if any(saved_name == name for saved_name, _ in self._savepoints):
            raise ValueError("savepoint name is already live")
        self._savepoints.append((name, self._writes.copy()))

    def _savepoint_index(self, name):
        name = _name(name)
        for index, (saved_name, _) in enumerate(self._savepoints):
            if saved_name == name:
                return index
        raise ValueError("unknown savepoint")

    def rollback_to(self, name):
        self._require_open()
        index = self._savepoint_index(name)
        self._writes = self._savepoints[index][1].copy()
        del self._savepoints[index + 1:]

    def release(self, name):
        self._require_open()
        index = self._savepoint_index(name)
        del self._savepoints[index:]

    def commit(self):
        self._require_open()
        observed_keys = self._reads | self._writes.keys()
        newer_records = self._engine._log[
            self._snapshot_version - self._engine._base_version:
        ]
        for record in newer_records:
            for key, _ in record["writes"]:
                if (key in observed_keys
                        or any(key.startswith(prefix) for prefix in self._prefixes)):
                    self._close()
                    raise Conflict("committed write conflicts with transaction")

        if self._writes:
            writes = [[key, self._writes[key]] for key in sorted(self._writes)]
            for key, value in writes:
                if value is None:
                    self._engine._data.pop(key, None)
                else:
                    self._engine._data[key] = value
            self._engine._version += 1
            self._engine._log.append(
                {"version": self._engine._version, "writes": writes}
            )
        version = self._engine._version
        self._close()
        return version

    def abort(self):
        self._require_open()
        self._close()
