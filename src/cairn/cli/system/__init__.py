"""Operational CLI command-family registrations for the top-level group."""

from ..main import main as main
from .doctor import (
    _check_ann as _check_ann,
    _check_embeddings as _check_embeddings,
    _enumerate_registrations as _enumerate_registrations,
    _FAIL as _FAIL,
    _PASS as _PASS,
    _WARN as _WARN,
    doctor as doctor,
)
from .metrics import metrics as metrics
from .report import _redact_paths as _redact_paths, report as report
from .status import eval_cmd as eval_cmd, status as status
from .sync import sync as sync

__all__ = [
    "doctor",
    "eval_cmd",
    "main",
    "metrics",
    "report",
    "status",
    "sync",
]
