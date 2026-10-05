import math
from collections import OrderedDict


class TTLCache:
    """A fixed-capacity cache with per-entry TTL and LRU eviction."""

    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")

        valid_ttl_type = isinstance(ttl, (int, float)) and not isinstance(ttl, bool)
        finite_ttl = not isinstance(ttl, float) or math.isfinite(ttl)
        if not valid_ttl_type or ttl <= 0 or not finite_ttl:
            raise ValueError("ttl must be a positive finite int or float")

        if not callable(clock):
            raise ValueError("clock must be callable")

        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._entries = OrderedDict()

    def _purge_expired(self, now):
        expired_keys = [
            key
            for key, (_, expires_at) in self._entries.items()
            if now >= expires_at
        ]
        for key in expired_keys:
            del self._entries[key]

    def put(self, key, value):
        now = self._clock()
        self._purge_expired(now)

        self._entries[key] = (value, now + self._ttl)
        self._entries.move_to_end(key)

        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def get(self, key, default=None):
        try:
            value, expires_at = self._entries[key]
        except KeyError:
            return default

        if self._clock() >= expires_at:
            del self._entries[key]
            return default

        self._entries.move_to_end(key)
        return value

    def delete(self, key):
        try:
            _, expires_at = self._entries[key]
        except KeyError:
            return False

        is_live = self._clock() < expires_at
        del self._entries[key]
        return is_live

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
