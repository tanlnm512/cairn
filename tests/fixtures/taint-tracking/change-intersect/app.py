"""Fixture: sql-flow plus the off-path pure helper format_label.

The sql-flow chain (handle_request -> validate_input -> run_query) sits on
a taint path; format_label touches no taint path and has no callers.
"""


def handle_request(urlopen, db):
    raw = urlopen("/requests/current")
    return validate_input(raw, db)


def validate_input(value, db):
    return run_query(value, db)


def run_query(query, db):
    cursor = db.cursor()
    return cursor.execute(query)


def format_label(label):
    return "[" + label + "]"
