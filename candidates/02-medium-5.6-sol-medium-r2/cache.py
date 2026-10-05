"""A fixed-size cache combining TTL expiration with LRU eviction."""

from collections import OrderedDict
import math


class TTLCache:
    """Store values until they expire, evicting least-recently-used keys."""

    def __init__(self, capacity, ttl, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
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
        # Oldest (least recently used) entries are at the beginning.
        self._entries = OrderedDict()

    def _purge_expired(self, now):
        # Expiration order and recency order are independent, so every entry
        # must be considered rather than only a prefix of the OrderedDict.
        expired = [
            key
            for key, (_value, expires_at) in self._entries.items()
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
            _value, expires_at = self._entries[key]
        except KeyError:
            return False

        is_live = self._clock() < expires_at
        del self._entries[key]
        return is_live

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)
