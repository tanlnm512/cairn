"""Service tests."""
from app.service import fetch_user


def test_fetch_user():
    assert fetch_user(1) is not None
