from collections import OrderedDict
import math


class TTLCache:
    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if (isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                or ttl <= 0 or (isinstance(ttl, float) and not math.isfinite(ttl))):
            raise ValueError("ttl must be a positive finite number")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._entries = OrderedDict()

    def _purge_expired(self, now):
        for key, (_, expires_at) in list(self._entries.items()):
            if now >= expires_at:
                del self._entries[key]

    def put(self, key, value):
        now = self._clock()
        self._purge_expired(now)
        self._entries[key] = (value, now + self._ttl)
        self._entries.move_to_end(key)
        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        if self._clock() >= entry[1]:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return entry[0]

    def delete(self, key):
        entry = self._entries.pop(key, None)
        return entry is not None and self._clock() < entry[1]

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
