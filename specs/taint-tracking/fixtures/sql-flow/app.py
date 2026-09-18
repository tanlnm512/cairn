"""Fixture: HTTP-parameter flow into SQL execution over three exact hops.

handle_request reads the request payload via ``urlopen`` and passes it
through validate_input into run_query, which executes it as SQL via the
``cursor.execute`` call tail.
"""


def handle_request(urlopen, db):
    raw = urlopen("/requests/current")
    return validate_input(raw, db)


def validate_input(value, db):
    return run_query(value, db)


def run_query(query, db):
    cursor = db.cursor()
    return cursor.execute(query)
