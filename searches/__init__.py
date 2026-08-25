"""Saved searches: one TOML file per car, read by every module that needs one.

This package is the dependency root. It imports no parser and knows nothing
about either website; the parsers import it and translate. See
:mod:`searches.definition` for what a search declares and :mod:`searches.cars`
for why the URL slugs are not here.
"""
from .cars import CARS, Car, UnknownCar, get as car
from .definition import (
    SEARCH_DIR,
    Band,
    CompetitorBounds,
    CompetitorFilters,
    DashboardSettings,
    SearchDefinition,
    SearchDefinitionError,
    available,
    for_run,
    from_provenance,
    load,
    load_all,
    parse,
    path_for,
)

__all__ = [
    "CARS", "Car", "UnknownCar", "car",
    "SEARCH_DIR", "Band", "CompetitorBounds", "CompetitorFilters",
    "DashboardSettings", "SearchDefinition", "SearchDefinitionError",
    "available", "for_run", "from_provenance", "load", "load_all", "parse",
    "path_for",
]
