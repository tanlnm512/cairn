"""Tests for shared parser factory caching."""

from __future__ import annotations

import pytest

from cairn.parsers.base import BaseParser
from cairn.parsers.factory import get_parser


@pytest.mark.parametrize("language", ["python", "go"])
def test_repeated_resolution_returns_cached_parser(language: str) -> None:
    parser = get_parser(language)

    assert isinstance(parser, BaseParser)
    assert get_parser(language) is parser
