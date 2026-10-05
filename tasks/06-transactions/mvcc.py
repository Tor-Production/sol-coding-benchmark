class Conflict(RuntimeError):
    pass

class Engine:
    def __init__(self, initial=None):
        raise NotImplementedError

    @property
    def version(self):
        raise NotImplementedError

    def begin(self):
        raise NotImplementedError

    def checkpoint(self):
        raise NotImplementedError

    def log_since(self, version):
        raise NotImplementedError

    @classmethod
    def restore(cls, checkpoint, records):
        raise NotImplementedError
