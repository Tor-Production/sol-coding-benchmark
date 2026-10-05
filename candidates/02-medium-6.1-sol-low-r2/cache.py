from collections import OrderedDict
import math


class TTLCache:
    """A bounded LRU cache whose entries expire independently of reads."""

    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or ttl <= 0
            or (isinstance(ttl, float) and not math.isfinite(ttl))
        ):
            raise ValueError("ttl must be a positive finite int or float")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._entries = OrderedDict()

    def _purge_expired(self, now):
        expired = [key for key, (_, expires_at) in self._entries.items()
                   if now >= expires_at]
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
        now = self._clock()
        if key not in self._entries:
            return default
        value, expires_at = self._entries[key]
        if now >= expires_at:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def delete(self, key):
        now = self._clock()
        if key not in self._entries:
            return False
        _, expires_at = self._entries.pop(key)
        return now < expires_at

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
