"""Guards that every test in this package needs.

The glossary (:mod:`banzai24.glossary`) is the one committed file the code
*writes* to, and both halves of that are hazards for a test suite: a test that
reads it passes or fails depending on which terms happen to have been glossed
this week, and a test that runs an extraction would append to the operator's
real file. Both are silent. So every test gets its own empty glossary, and a
test that wants entries in it says so.
"""
from __future__ import annotations

import pytest

from banzai24 import glossary


@pytest.fixture(autouse=True)
def isolated_glossary(tmp_path, monkeypatch):
    """Point the glossary at a throwaway file for the duration of one test."""
    path = tmp_path / "glossary.json"
    monkeypatch.setattr(glossary, "PATH", path)
    return path
