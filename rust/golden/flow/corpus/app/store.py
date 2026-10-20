"""Storage helpers."""


def get_db(path):
    return Db(path)


class Db:
    def cursor(self):
        return Cur()

    def commit(self):
        pass


class Cur:
    def execute(self, sql, *params):
        return None
