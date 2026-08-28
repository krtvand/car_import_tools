"""banzai24's half of a saved search: the .toml turned into this module's filters.

The file itself lives in :mod:`searches` and is shared with ``bazaraki`` and the
dashboard. This module is the adapter: it takes the parts of it banzai24 owns —
``[site]``, ``[api]``, ``[sheet]`` — checks them against banzai24's own field
names, and names the car in banzai24's own vocabulary (:mod:`banzai24.cars`).
:mod:`searches` cannot do that check itself, and deliberately does not try: it
would have to import this package to know what a ``[site]`` key is called.

**Year and mileage are not read from the file.** They are the union of the
search's ``[[band]]`` list, because a band is a mileage range with a price on
it, and a bound written separately from the prices underneath it drifts. It had
already drifted twice when this was written.

**The file is the source of truth, not the run.** ``fetch`` records the search's
name in ``lots.json`` and ``report`` loads the file again by that name, so
re-tuning a requirement and re-rendering this morning's run costs nothing. Since
the max bids moved into that same file, re-rendering now also re-prices — see
``docs/adr/0004-bid-prices-are-read-live.md``, which is where that trade was
made deliberately rather than inherited by accident.

The definition is also copied into ``lots.json`` as provenance, and read back
only when the named file has since been deleted or renamed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import searches
from searches.definition import Band, SearchDefinitionError

from . import cars as banzai_cars
from .config import AuctionFilters
from .lot_filters import LotFilters
from .requirements import SheetRequirements

# Kept as names on this module so `search.SEARCH_DIR` and
# `except search.SearchDefinitionError` keep meaning what they always did.
SEARCH_DIR = searches.SEARCH_DIR
SUFFIX = ".toml"

__all__ = ["SEARCH_DIR", "SUFFIX", "SearchDefinition", "SearchDefinitionError",
           "available", "for_run", "from_provenance", "load", "parse", "path_for"]

# TOML key -> AuctionFilters field, for the keys whose names differ.
_SITE_ALIASES = {"grade": "grade_origin"}

# Set from the car and from the bands respectively, so a file may not name them.
# `searches` rejects them before we get here; this set is what keeps them out of
# the "known keys" list in an error message that would otherwise invite the typo.
_NOT_FROM_FILE = {"make", "model",
                  "year_start", "year_end", "mileage_start", "mileage_end"}

_SITE_KEYS = {f.name for f in fields(AuctionFilters)} - _NOT_FROM_FILE

# `[auction_statistics]` overrides `[site]`, so it takes the same keys — minus
# the two the archive search names for itself. `source` and `status` are what a
# statistics search *is* rather than facts about a car: an archive of anything
# but completed sales has no hammer price on it, and a file able to say
# `source = "auctions"` here could ask for statistics over lots that have not
# been sold yet.
_STATS_FIXED = {"source", "status"}
_STATS_KEYS = _SITE_KEYS - _STATS_FIXED
_API_KEYS = {f.name for f in fields(LotFilters)}
_SHEET_KEYS = {f.name for f in fields(SheetRequirements)}


def _tuple_of_str(value, where: str) -> tuple[str, ...]:
    if isinstance(value, str):
        # A bare string where a list belongs is the easy typo, and silently
        # iterating it into characters would be a filter that matches nothing.
        raise SearchDefinitionError(f"{where} must be a list, not a string ({value!r})")
    # Tuples as well as lists: TOML only ever produces lists, but a definition
    # read back from a run's own provenance comes through `asdict`, which keeps
    # the dataclass's tuples as tuples.
    if (not isinstance(value, (list, tuple))
            or not all(isinstance(item, str) for item in value)):
        raise SearchDefinitionError(f"{where} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _known(section: str, keys, allowed: set[str], where: str) -> None:
    """An unrecognised key is an error, never a shrug.

    A misspelled ``drivetrian`` that loaded as nothing would be a search silently
    running without its requirement — a wrong report that still renders, which is
    the failure this codebase is written against.
    """
    for key in keys:
        if key not in allowed:
            raise SearchDefinitionError(
                f"{where}: [{section}] has no key {key!r}. "
                f"Known keys: {', '.join(sorted(allowed))}"
            )


@dataclass(frozen=True)
class SearchDefinition:
    """One car's search as banzai24 needs it: filters, requirements, bands."""

    name: str
    filters: AuctionFilters
    lot_filters: LotFilters = field(default_factory=LotFilters)
    requirements: SheetRequirements = field(default_factory=SheetRequirements)
    bands: tuple[Band, ...] = ()
    stats_overrides: dict = field(default_factory=dict)   # `[auction_statistics]`
    spec: searches.SearchDefinition | None = None   # the shared file behind this
    source: Path | None = None       # None when read back from run provenance

    def stats_filters(self, band: Band) -> AuctionFilters:
        """The archive search for one band — `[site]`, overridden, then pinned.

        Three layers, narrowest last. `[site]` is the base, because a statistics
        search that did not share the buy-side filters would be measuring a
        different car from the one being bought. `[auction_statistics]` overrides
        it, which is how the trim line (`model_grade`) narrows the measurement
        without narrowing tomorrow's fetch. Then the band pins year and mileage,
        the same way it does everywhere else, and the archive pins itself.

        Year is an exact match rather than the search-wide span: a band is the
        thing that has a price on it, so the sales it is measured against are
        the ones in its own year and its own mileage range.
        """
        return replace(
            self.filters,
            **self.stats_overrides,
            year_start=band.year,
            year_end=band.year,
            mileage_start=band.mileage_start,
            mileage_end=band.mileage_end,
            source="archive",
            status="SOLD",
        )

    @property
    def stats_declared(self) -> bool:
        """Did the file say anything about statistics at all?

        An absent section is not an error — a search may simply not want the
        page — but it is different from an empty one, and the caller says so
        rather than rendering a panel that looks like "no sales found".
        """
        return bool(self.stats_overrides)

    def describe(self) -> str:
        bits = [f"[site] {', '.join(_describe_site(self.filters))}"]
        if self.lot_filters.active:
            bits.append(f"[api] {self.lot_filters.describe()}")
        if self.requirements.active:
            bits.append(f"[sheet] {self.requirements.describe()}")
        if self.stats_overrides:
            shown = ", ".join(f"{k}={v}" for k, v in sorted(self.stats_overrides.items()))
            bits.append(f"[auction_statistics] {shown}")
        if self.bands:
            bits.append(f"{len(self.bands)} band{'' if len(self.bands) == 1 else 's'}")
        return " · ".join(bits)

    def to_payload(self) -> dict:
        """The provenance copy written into ``lots.json``.

        Delegated to the shared definition so there is one spelling of what a
        search *is* on disk, rather than banzai24's view of one.
        """
        if self.spec is not None:
            return self.spec.to_payload()
        return {"name": self.name}


def _describe_site(filters: AuctionFilters) -> list[str]:
    return [
        f"{f.name}={value}"
        for f in fields(filters)
        if (value := getattr(filters, f.name)) not in (None, (), "")
    ]


def adapt(spec: searches.SearchDefinition) -> SearchDefinition:
    """Turn a shared definition into banzai24's filters and requirements."""
    where = spec.source.name if spec.source else f"{spec.name}{SUFFIX}"

    site = {_SITE_ALIASES.get(key, key): value
            for key, value in (spec.sections.get("site") or {}).items()}
    _known("site", site, _SITE_KEYS, where)
    for key in ("grade_origin", "model_grade"):
        if key in site:
            site[key] = _tuple_of_str(site[key], f"{where}: [site] {key}")

    make, model = banzai_cars.slugs(spec.car)
    site.update(
        make=make, model=model,
        year_start=spec.year_start, year_end=spec.year_end,
        mileage_start=spec.mileage_start, mileage_end=spec.mileage_end,
    )

    api = dict(spec.sections.get("api") or {})
    _known("api", api, _API_KEYS, where)
    for key in ("body_model_code", "exclude_colours", "exclude_model_grades"):
        if key in api:
            api[key] = _tuple_of_str(api[key], f"{where}: [api] {key}")

    stats = {_SITE_ALIASES.get(key, key): value
             for key, value in (spec.sections.get("auction_statistics") or {}).items()}
    _known("auction_statistics", stats, _STATS_KEYS, where)
    for key in ("grade_origin", "model_grade"):
        if key in stats:
            stats[key] = _tuple_of_str(stats[key], f"{where}: [auction_statistics] {key}")

    sheet = dict(spec.sections.get("sheet") or {})
    _known("sheet", sheet, _SHEET_KEYS, where)
    if "no_damage_codes" in sheet:
        sheet["no_damage_codes"] = _tuple_of_str(
            sheet["no_damage_codes"], f"{where}: [sheet] no_damage_codes")
    if "drivetrain" in sheet and not isinstance(sheet["drivetrain"], str):
        raise SearchDefinitionError(f"{where}: [sheet] drivetrain must be a string")

    try:
        return SearchDefinition(
            name=spec.name,
            filters=AuctionFilters(**site),
            lot_filters=LotFilters(**api),
            requirements=SheetRequirements(**sheet),
            bands=spec.bands,
            stats_overrides=stats,
            spec=spec,
            source=spec.source,
        )
    except TypeError as exc:      # a key of the right name but the wrong shape
        raise SearchDefinitionError(f"{where}: {exc}") from None
    except banzai_cars.UnknownCar as exc:
        raise SearchDefinitionError(f"{where}: {exc.args[0]}") from None


def parse(payload: dict, name: str, path: Path | None = None) -> SearchDefinition:
    """Build a definition from already-decoded TOML (or from run provenance)."""
    return adapt(searches.parse(payload, name=name, path=path))


def path_for(name: str) -> Path:
    return searches.path_for(name)


def available(directory: Path | None = None) -> list[str]:
    """Every saved search's name, alphabetically. Used by the CLI's error text."""
    return searches.available(directory)


def load(name: str, directory: Path | None = None) -> SearchDefinition:
    """Read one saved search by name. Raises if it is absent or malformed."""
    spec = searches.load(name, directory)
    try:
        return adapt(spec)
    except banzai_cars.UnknownCar as exc:
        raise SearchDefinitionError(exc.args[0]) from None


def from_provenance(payload: dict) -> SearchDefinition | None:
    """The copy ``fetch`` wrote into ``lots.json``, or ``None`` if it has none."""
    spec = searches.from_provenance(payload)
    if spec is None:
        return None
    try:
        return adapt(spec)
    except (SearchDefinitionError, banzai_cars.UnknownCar):
        return None


def for_run(payload: dict) -> tuple[SearchDefinition | None, str | None]:
    """``(definition, problem)`` for one run's ``lots.json``.

    The named file wins so that re-tuning a requirement re-judges an existing
    run for free. The provenance copy is the fallback, and using it is worth
    saying out loud: the report is then judging against what the search *was*,
    which may not be what the file says now.
    """
    spec, problem = searches.for_run(payload)
    if spec is None:
        return None, problem
    try:
        return adapt(spec), problem
    except (SearchDefinitionError, banzai_cars.UnknownCar) as exc:
        return None, str(exc.args[0] if exc.args else exc)
