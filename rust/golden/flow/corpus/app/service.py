"""User service."""
import json


def fetch_user(uid):
    raw = load_payload(uid)
    return json.loads(raw)


def load_payload(uid):
    with open(f"u{uid}.json") as fh:
        return fh.read()


def save_user(user):
    conn = connect()
    conn.cursor().execute("INSERT", user)


def connect():
    from app.store import get_db
    return get_db("app.db")
