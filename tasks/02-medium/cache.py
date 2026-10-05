class TTLCache:
    def __init__(self, capacity, ttl, clock):
        raise NotImplementedError("Implement the task contract")

    def put(self, key, value):
        raise NotImplementedError

    def get(self, key, default=None):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def __len__(self):
        raise NotImplementedError
