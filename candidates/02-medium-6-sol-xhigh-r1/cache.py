"""A time-to-live cache with least-recently-used capacity eviction."""

import math
from collections import OrderedDict
from fractions import Fraction


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
        self._purge_expired(now)
        try:
            expires_at = now + self.ttl
        except OverflowError:
            # A valid, arbitrarily large int TTL cannot always be added to a float.
            expires_at = Fraction(now) + self.ttl
        self._entries[key] = (value, expires_at)
        self._entries.move_to_end(key)
        if len(self._entries) > self.capacity:
            self._entries.popitem(last=False)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if self.clock() >= expires_at:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def delete(self, key):
        entry = self._entries.pop(key, None)
        return entry is not None and self.clock() < entry[1]

    def __len__(self):
        self._purge_expired(self.clock())
        return len(self._entries)
