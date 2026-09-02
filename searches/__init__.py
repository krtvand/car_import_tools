"""Saved searches: one TOML file per car, read by every module that needs one.

This package imports no parser and knows nothing about either website; the
parsers import it and translate. The only thing under it is :mod:`cars`, which
holds what is true of a car rather than of a search — and which the names below
are re-exported from, so a caller that has a search already has its car. See
:mod:`searches.definition` for what a search declares and :mod:`cars` for why
the URL slugs are in neither package.
"""
from cars.definitions import CARS, Car, UnknownCar, get as car
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
