import math
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, ttl, clock):
        if (type(capacity) is not int or capacity <= 0
                or type(ttl) not in (int, float) or not math.isfinite(ttl)
                or ttl <= 0 or not callable(clock)):
            raise ValueError("Invalid cache arguments")
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self.data = OrderedDict()

    def _purge(self):
        now = self.clock()
        for key, (_, expiry) in list(self.data.items()):
            if now >= expiry:
                del self.data[key]
        return now

    def put(self, key, value):
        now = self._purge()
        self.data[key] = (value, now + self.ttl)
        self.data.move_to_end(key)
        while len(self.data) > self.capacity:
            self.data.popitem(last=False)

    def get(self, key, default=None):
        self._purge()
        if key not in self.data:
            return default
        self.data.move_to_end(key)
        return self.data[key][0]

    def delete(self, key):
        self._purge()
        if key not in self.data:
            return False
        del self.data[key]
        return True

    def __len__(self):
        self._purge()
        return len(self.data)
