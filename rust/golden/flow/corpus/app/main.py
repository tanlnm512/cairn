"""Demo entry points."""
from app.cycle_a import a, recurse
from app.pipeline import run_pipeline
from app.service import fetch_user, save_user


def main():
    user = fetch_user(1)
    save_user(user)
    return user


def kickoff():
    a()
    run_pipeline()
    recurse(3)
