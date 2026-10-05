"""A small TTL cache with least recently used eviction."""

import math
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or ttl <= 0
            or (isinstance(ttl, float) and not math.isfinite(ttl))
        ):
            raise ValueError("ttl must be a positive finite number")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._entries = OrderedDict()

    def _purge_expired(self, now):
        for key, (_, expires_at) in list(self._entries.items()):
            if now >= expires_at:
                del self._entries[key]

    def put(self, key, value):
        now = self.clock()
        if key in self._entries:
            del self._entries[key]
        elif len(self._entries) >= self.capacity:
            self._purge_expired(now)
            if len(self._entries) >= self.capacity:
                self._entries.popitem(last=False)
        self._entries[key] = (value, now + self.ttl)

    def get(self, key, default=None):
        now = self.clock()
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if now >= expires_at:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def delete(self, key):
        now = self.clock()
        entry = self._entries.pop(key, None)
        return entry is not None and now < entry[1]

    def __len__(self):
        self._purge_expired(self.clock())
        return len(self._entries)
