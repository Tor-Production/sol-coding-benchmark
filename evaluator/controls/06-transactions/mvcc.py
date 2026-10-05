"""Reference implementation of the explicitly conservative OCC contract."""
from copy import deepcopy

class Conflict(RuntimeError):
    pass

def valid_int(value, nonnegative=False):
    return isinstance(value, int) and not isinstance(value, bool) and (not nonnegative or value >= 0)

def key(value):
    if not isinstance(value, str) or not value:
        raise ValueError('key/name')

def data(value):
    if not isinstance(value, dict):
        raise ValueError('data')
    for k, v in value.items():
        key(k)
        if not valid_int(v):
            raise ValueError('value')

class Engine:
    def __init__(self, initial=None):
        if initial is None:
            initial = {}
        data(initial)
        self._data = dict(initial)
        self._version = self._base = 0
        self._log = []

    @property
    def version(self):
        return self._version

    def begin(self):
        return Transaction(self)

    def checkpoint(self):
        return dict(version=self._version, data=dict(sorted(self._data.items())))

    def log_since(self, version):
        if not valid_int(version, True) or not self._base <= version <= self._version:
            raise ValueError('version')
        return deepcopy([r for r in self._log if r['version'] > version])

    @classmethod
    def restore(cls, checkpoint, records):
        if not isinstance(checkpoint, dict) or set(checkpoint) != {'version', 'data'} or not valid_int(checkpoint['version'], True):
            raise ValueError('checkpoint')
        data(checkpoint['data'])
        if not isinstance(records, list):
            raise ValueError('records')
        version = checkpoint['version']
        for r in records:
            if not isinstance(r, dict) or set(r) != {'version', 'writes'} or not valid_int(r['version'], True) or r['version'] != version + 1:
                raise ValueError('record version')
            writes = r['writes']
            if not isinstance(writes, list) or not writes:
                raise ValueError('writes')
            last = None
            for pair in writes:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError('pair')
                k, v = pair; key(k)
                if last is not None and k <= last:
                    raise ValueError('write order')
                if v is not None and not valid_int(v):
                    raise ValueError('write value')
                last = k
            version += 1
        e = cls(checkpoint['data'])
        e._base = e._version = checkpoint['version']
        e._log = deepcopy(records)
        for r in e._log:
            for k, v in r['writes']:
                if v is None:
                    e._data.pop(k, None)
                else:
                    e._data[k] = v
            e._version = r['version']
        return e

class Transaction:
    def __init__(self, engine):
        self._engine = engine
        self._snapshot = dict(engine._data)
        self._start = engine.version
        self._writes = {}
        self._reads = set()
        self._ranges = set()
        self._points = []
        self._active = True

    def _open(self):
        if not self._active:
            raise RuntimeError('closed transaction')

    def get(self, name):
        self._open(); key(name)
        self._reads.add(name)
        return self._writes.get(name, self._snapshot.get(name))

    def scan(self, prefix=''):
        self._open()
        if not isinstance(prefix, str):
            raise ValueError('prefix')
        self._ranges.add(prefix)
        visible = dict(self._snapshot)
        for k, v in self._writes.items():
            if v is None:
                visible.pop(k, None)
            else:
                visible[k] = v
        return dict(sorted((k, v) for k, v in visible.items() if k.startswith(prefix)))

    def put(self, name, value):
        self._open(); key(name)
        if not valid_int(value):
            raise ValueError('value')
        self._writes[name] = value

    def delete(self, name):
        self._open(); key(name)
        self._writes[name] = None

    def savepoint(self, name):
        self._open(); key(name)
        if any(n == name for n, _ in self._points):
            raise ValueError('duplicate savepoint')
        self._points.append((name, dict(self._writes)))

    def _point(self, name):
        self._open(); key(name)
        for i, (n, _) in enumerate(self._points):
            if n == name:
                return i
        raise ValueError('unknown savepoint')

    def rollback_to(self, name):
        i = self._point(name)
        self._writes = dict(self._points[i][1])
        self._points = self._points[:i+1]

    def release(self, name):
        i = self._point(name)
        self._points = self._points[:i]

    def abort(self):
        self._open(); self._active = False

    def commit(self):
        self._open()
        e = self._engine
        for r in e._log:
            if r['version'] > self._start:
                for key, _ in r['writes']:
                    if key in self._reads or key in self._writes or any(key.startswith(p) for p in self._ranges):
                        self._active = False
                        raise Conflict('serialization conflict')
        if self._writes:
            e._version += 1
            e._log.append(dict(version=e._version, writes=[[k, v] for k, v in sorted(self._writes.items())]))
            for k, v in self._writes.items():
                if v is None:
                    e._data.pop(k, None)
                else:
                    e._data[k] = v
        self._active = False
        return e._version
