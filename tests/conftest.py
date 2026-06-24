"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FIXTURES = Path(__file__).parent / "fixtures"


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(), "html.parser")


@pytest.fixture
def list_soup() -> BeautifulSoup:
    return _soup("list_page.html")


@pytest.fixture
def detail_soup() -> BeautifulSoup:
    return _soup("detail_page.html")