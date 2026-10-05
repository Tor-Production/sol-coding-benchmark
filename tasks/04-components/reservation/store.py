class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Store:
    def __init__(self, db_path):
        raise NotImplementedError("Implement the task contract")

    def add_item(self, sku, quantity):
        raise NotImplementedError

    def get_item(self, sku):
        raise NotImplementedError

    def reserve(self, key, sku, quantity):
        raise NotImplementedError

    def release(self, reservation_id):
        raise NotImplementedError

    def report(self):
        raise NotImplementedError
