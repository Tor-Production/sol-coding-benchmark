"""A fixed-capacity cache combining TTL expiry with LRU eviction."""

import math
from collections import OrderedDict


class TTLCache:
    """Store values until their TTL expires, evicting live values by LRU."""

    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")

        valid_ttl = (
            not isinstance(ttl, bool)
            and isinstance(ttl, (int, float))
            and ttl > 0
            and (isinstance(ttl, int) or math.isfinite(ttl))
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
        """Remove every entry expired at *now*, regardless of recency."""
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

        _, expires_at = entry
        is_live = self._clock() < expires_at
        del self._entries[key]
        return is_live

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
