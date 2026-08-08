"""Search filters for banzai24.com and the UI URLs they build.

banzai24 is a Nuxt SPA. Its UI URL carries human-friendly slugs
(``/MAZDA/CX-30/transmissions-auto``) while the JSON API underneath wants
numeric ids (``models[]=11528``). Because :mod:`banzai24.fetch` drives the real
page rather than calling the API directly, **we only ever build UI URLs** — the
slug-to-id resolution stays the app's problem, not ours. That is the main reason
the plan's "resolve make/model to a modelId" step disappeared.

Mirrors the shape of :mod:`bazaraki.config`: one dataclass listing every
supported filter, plus a builder that turns it into a URL.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from urllib.parse import urlencode

BASE_URL = "https://banzai24.com"

# Grades an auction inspector can award overall. Used for validation only —
# the site takes them as opaque strings.
KNOWN_GRADES = ("S", "6", "5", "4.5", "4", "3.5", "3", "2", "1", "R", "RA")


@dataclass
class AuctionFilters:
    """What to search for on banzai24.

    ``make``/``model`` are the URL slugs as they appear on the site (upper-case
    for the make, e.g. ``MAZDA`` / ``CX-30``). ``grade_origin`` is multi-select:
    each value is emitted as its own repeated ``gradeOrigin`` query parameter,
    the same convention bazaraki uses for its multi-selects.
    """

    make: str = "MAZDA"
    model: str | None = "CX-30"
    transmission: str | None = "auto"       # "auto" | "manual"; None = any

    year_start: int | None = None
    year_end: int | None = None
    mileage_start: int | None = None
    mileage_end: int | None = None
    engine_capacity_start: float | None = None
    engine_capacity_end: float | None = None

    grade_origin: tuple[str, ...] = ()

    # "auctions" = lots still to be sold; "archive" = completed sales.
    source: str = "auctions"
    country_iso: str = "JP"


DEFAULT_FILTERS = AuctionFilters(
    make="MAZDA",
    model="CX-30",
    transmission="auto",
    year_start=2023,
    year_end=2023,
    mileage_end=55000,
    engine_capacity_start=1.9,
    grade_origin=("4", "4.5", "5"),
)


# Dataclass field -> query parameter name. Range params follow the site's
# Start/End suffix convention; only mileageEnd and engineCapacityStart were seen
# live, the opposite bounds are inferred from that symmetry.
_QUERY_PARAMS: dict[str, str] = {
    "year_start": "yearStart",
    "year_end": "yearEnd",
    "mileage_start": "mileageStart",
    "mileage_end": "mileageEnd",
    "engine_capacity_start": "engineCapacityStart",
    "engine_capacity_end": "engineCapacityEnd",
    "source": "source",
    "country_iso": "countryISO",
}


def base_path(filters: AuctionFilters) -> str:
    """The slug path: ``/MAZDA/CX-30/transmissions-auto``.

    Model and transmission are both optional; omitting them widens the search.
    """
    parts = [filters.make]
    if filters.model:
        parts.append(filters.model)
    if filters.transmission:
        parts.append(f"transmissions-{filters.transmission}")
    return "/" + "/".join(parts)


def build_search_url(filters: AuctionFilters | None = None) -> str:
    """Full UI URL for a filter set, ready for Playwright to navigate to."""
    filters = filters or DEFAULT_FILTERS

    # list of pairs (not a dict) so grade_origin can repeat its key
    params: list[tuple[str, str]] = []
    for name, param in _QUERY_PARAMS.items():
        value = getattr(filters, name)
        if value is not None:
            params.append((param, str(value)))
    for grade in filters.grade_origin:
        params.append(("gradeOrigin", str(grade)))

    query = urlencode(params)
    return f"{BASE_URL}{base_path(filters)}" + (f"?{query}" if query else "")


def run_slug(filters: AuctionFilters) -> str:
    """Filesystem-safe label for a run directory, e.g. ``MAZDA-CX-30``."""
    parts = [filters.make] + ([filters.model] if filters.model else [])
    slug = "-".join(parts)
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)


def describe(filters: AuctionFilters) -> str:
    """One-line human summary of the non-default filters, for run logs."""
    shown = []
    for f in fields(filters):
        value = getattr(filters, f.name)
        if value in (None, (), ""):
            continue
        shown.append(f"{f.name}={value}")
    return ", ".join(shown)