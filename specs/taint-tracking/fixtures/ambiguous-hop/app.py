"""Fixture: HTTP-parameter flow whose middle hop resolves to two definitions.

handle_upload reads the request payload via ``urlopen`` and calls
``transform``, a name defined in two modules; run_migration executes the
value as SQL via the ``cursor.execute`` call tail. Default propagation
stops at the ambiguous hop; the fuzzy opt-in crosses it.
"""


def handle_upload(urlopen, db, transform, run_migration):
    payload = urlopen("/uploads/current")
    return transform(payload, db, run_migration)


def run_migration(statement, db):
    cursor = db.cursor()
    return cursor.execute(statement)
