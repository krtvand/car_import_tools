"""Backwards-compatible entry point: ``python main.py`` -> ``python -m bazaraki``.

The scraper moved into the ``bazaraki`` package when ``banzai24`` was added as a
second project; this shim keeps the documented commands working.
"""
from bazaraki.cli import main

if __name__ == "__main__":
    main()