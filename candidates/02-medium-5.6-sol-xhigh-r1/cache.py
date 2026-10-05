import math
from collections import OrderedDict


class TTLCache:
    """A fixed-size least-recently-used cache with per-entry expiration."""

    def __init__(self, capacity, ttl, clock):
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
        ):
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

        if key not in self._entries and len(self._entries) >= self._capacity:
            self._entries.popitem(last=False)

        self._entries[key] = (value, now + self._ttl)
        self._entries.move_to_end(key)

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

        if self._clock() >= expires_at:
            del self._entries[key]
            return False

        del self._entries[key]
        return True

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
