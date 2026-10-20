"""Pipeline: config read flows into query execution."""
from app.store import get_db


def read_config():
    with open("cfg.json") as fh:
        cfg = fh.read()
    dispatch(cfg)
    return cfg


def dispatch(cfg):
    run_query(cfg)


def run_query(cfg):
    db = get_db("pipeline.db")
    db.cursor().execute(cfg)


def run_pipeline():
    dispatch(read_config())
