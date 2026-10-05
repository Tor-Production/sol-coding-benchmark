"""A small in-memory TTL cache with least-recently-used eviction."""

from collections import OrderedDict
import math


class TTLCache:
    """Store up to *capacity* values for a fixed amount of time."""

    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")

        # Check integers separately: converting an arbitrarily large integer to a
        # float merely to call isfinite() can raise OverflowError.
        valid_ttl = (
            isinstance(ttl, int)
            and not isinstance(ttl, bool)
            and ttl > 0
        ) or (
            isinstance(ttl, float)
            and math.isfinite(ttl)
            and ttl > 0
        )
        if not valid_ttl:
            raise ValueError("ttl must be a positive finite number")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._entries = OrderedDict()

    def _purge_expired(self, now):
        expired = [
            key
            for key, (_, expires_at) in self._entries.items()
            if now >= expires_at
        ]
        for key in expired:
            del self._entries[key]

    def put(self, key, value):
        now = self._clock()
        self._purge_expired(now)

        # Assignment replaces the value but does not change OrderedDict order,
        # so move_to_end is needed for both replacements and new entries.
        self._entries[key] = (value, now + self._ttl)
        self._entries.move_to_end(key)
        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default

        value, expires_at = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return default

        self._entries.move_to_end(key)
        return value

    def delete(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False

        if self._clock() >= entry[1]:
            del self._entries[key]
            return False

        del self._entries[key]
        return True

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
