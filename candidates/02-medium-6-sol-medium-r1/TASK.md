# TTL cache with LRU eviction

Implement `TTLCache(capacity, ttl, clock)` in `cache.py`, using only the Python
standard library. `clock` is a callable returning monotonic seconds; all time
must come from it. `capacity` must be a positive integer (not bool), `ttl` a
positive finite int/float (not bool), and `clock` callable; otherwise ValueError.
Keys are hashable. Values may be any object, including None.

Methods:

- `put(key, value)` stores/replaces a value and resets that key's expiration to
  `clock() + ttl`. It makes the key most recently used.
- `get(key, default=None)` returns a live value and makes it most recently used;
  missing/expired entries return default. A read does NOT extend the TTL.
- `delete(key)` returns True only if a live entry was removed.
- `len(cache)` counts live entries only.

Expiry is inclusive: an entry is expired when `now >= expires_at`. Expired keys
must never evict a live key: purge expired entries before capacity eviction,
even if an expired key is not the least recently used. Evict the least recently
used live key when necessary. Separate instances must not share state. O(1)
recency operations are expected; an O(capacity) expiry scan is acceptable.
Thread safety and clock rollback are out of scope.

Run `python -m unittest discover -s tests -v`. Add tests if useful, preserve
supplied tests and contract, and summarize your work. No external dependencies,
network access, or subagents.
