"""A saved search: one TOML file that is the whole declaration for one car.

``banzai24/searches/mazda-cx30.toml`` holds everything wanted from a CX-30, in
three sections named for *who checks them* — see :mod:`banzai24.requirements`
for what that distinction decides.

**The file is the source of truth, not the run.** ``fetch`` records the search's
name in ``lots.json`` and ``report`` loads the file again by that name, so
re-tuning a requirement and re-rendering this morning's run costs nothing — the
same bargain :mod:`banzai24.bidding` strikes with its price tables, and for the
same reason: a stored answer goes stale silently.

The definition *is* also copied into ``lots.json`` verbatim, but only as
provenance — what this run was judged by on the day — and it is read back only
when the named file has since been deleted or renamed. A report that renders
against a definition that no longer exists says so rather than skipping the
requirements.

Nothing is inherited from anywhere. That is the whole reason this replaced the
old ``--no-defaults`` shell wrappers: a saved search that omitted a filter used
to silently pick up ``config.DEFAULT_FILTERS``' value for it, so a RAV4 search
could inherit the CX-30's engine-capacity floor.
"""
from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import AuctionFilters
from .lot_filters import LotFilters
from .requirements import SheetRequirements

SEARCH_DIR = Path(__file__).parent / "searches"
SUFFIX = ".toml"

# TOML key -> AuctionFilters field, for the keys whose names differ. Everything
# else in [site] is spelled exactly as the dataclass field is.
_SITE_ALIASES = {"grade": "grade_origin"}

_SITE_KEYS = {f.name for f in fields(AuctionFilters)}
_API_KEYS = {f.name for f in fields(LotFilters)}
_SHEET_KEYS = {f.name for f in fields(SheetRequirements)}

_SECTIONS = ("site", "api", "sheet")


class SearchDefinitionError(ValueError):
    """The file is on disk but cannot be trusted — an unknown key, a bad type.

    Raised rather than warned about, unlike a bad price table. A price table
    that fails to load costs you a column; a search definition that fails to
    load would mean fetching the wrong car, or judging it against half a list.
    """


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


def _known(section: str, keys, allowed: set[str], path: Path) -> None:
    """An unrecognised key is an error, never a shrug.

    A misspelled ``millage_end`` that loaded as nothing would be a search
    silently running without its mileage bound — a wrong report that still
    renders, which is the failure this whole codebase is written against.
    """
    for key in keys:
        if key not in allowed:
            raise SearchDefinitionError(
                f"{path.name}: [{section}] has no key {key!r}. "
                f"Known keys: {', '.join(sorted(allowed))}"
            )


@dataclass(frozen=True)
class SearchDefinition:
    """One car's whole search, ready for ``fetch`` and for ``report``."""

    name: str
    filters: AuctionFilters
    lot_filters: LotFilters = field(default_factory=LotFilters)
    requirements: SheetRequirements = field(default_factory=SheetRequirements)
    source: Path | None = None       # None when read back from run provenance

    def describe(self) -> str:
        bits = [f"[site] {', '.join(_describe_site(self.filters))}"]
        if self.lot_filters.active:
            bits.append(f"[api] {self.lot_filters.describe()}")
        if self.requirements.active:
            bits.append(f"[sheet] {self.requirements.describe()}")
        return " · ".join(bits)

    def to_payload(self) -> dict:
        """The provenance copy written into ``lots.json``."""
        return {
            "name": self.name,
            "site": asdict(self.filters),
            "api": asdict(self.lot_filters),
            "sheet": asdict(self.requirements),
        }


def _describe_site(filters: AuctionFilters) -> list[str]:
    return [
        f"{f.name}={value}"
        for f in fields(filters)
        if (value := getattr(filters, f.name)) not in (None, (), "")
    ]


def parse(payload: dict, name: str, path: Path | None = None) -> SearchDefinition:
    """Build a definition from already-decoded TOML (or from run provenance)."""
    where = path or Path(f"{name}{SUFFIX}")

    if extra := set(payload) - set(_SECTIONS):
        raise SearchDefinitionError(
            f"{where.name}: unknown section(s) {', '.join(sorted(extra))}. "
            f"Known sections: {', '.join(_SECTIONS)}"
        )

    site = dict(payload.get("site") or {})
    site = {_SITE_ALIASES.get(key, key): value for key, value in site.items()}
    _known("site", site, _SITE_KEYS, where)
    if "grade_origin" in site:
        site["grade_origin"] = _tuple_of_str(site["grade_origin"], f"{where.name}: [site] grade")
    if not site.get("make"):
        raise SearchDefinitionError(f"{where.name}: [site] make is required")

    api = dict(payload.get("api") or {})
    _known("api", api, _API_KEYS, where)
    if "body_model_code" in api:
        api["body_model_code"] = _tuple_of_str(
            api["body_model_code"], f"{where.name}: [api] body_model_code")

    sheet = dict(payload.get("sheet") or {})
    _known("sheet", sheet, _SHEET_KEYS, where)
    if "no_damage_codes" in sheet:
        sheet["no_damage_codes"] = _tuple_of_str(
            sheet["no_damage_codes"], f"{where.name}: [sheet] no_damage_codes")
    if "drivetrain" in sheet and not isinstance(sheet["drivetrain"], str):
        raise SearchDefinitionError(f"{where.name}: [sheet] drivetrain must be a string")

    try:
        return SearchDefinition(
            name=name,
            filters=AuctionFilters(**site),
            lot_filters=LotFilters(**api),
            requirements=SheetRequirements(**sheet),
            source=path,
        )
    except TypeError as exc:      # a key of the right name but the wrong shape
        raise SearchDefinitionError(f"{where.name}: {exc}") from None


def path_for(name: str) -> Path:
    return SEARCH_DIR / f"{name}{SUFFIX}"


def available(directory: Path | None = None) -> list[str]:
    """Every saved search's name, alphabetically. Used by the CLI's error text."""
    directory = directory or SEARCH_DIR
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob(f"*{SUFFIX}"))


def load(name: str, directory: Path | None = None) -> SearchDefinition:
    """Read one saved search by name. Raises if it is absent or malformed."""
    directory = directory or SEARCH_DIR
    path = directory / f"{name}{SUFFIX}"
    if not path.exists():
        known = ", ".join(available(directory)) or "none found"
        raise SearchDefinitionError(f"No search named {name!r}. Available: {known}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise SearchDefinitionError(f"{path.name}: {exc}") from None
    return parse(payload, name=name, path=path)


def from_provenance(payload: dict) -> SearchDefinition | None:
    """The copy ``fetch`` wrote into ``lots.json``, or ``None`` if it has none.

    Runs fetched before saved searches became files have no record of one, and
    render ungrouped rather than being judged against a list nobody declared.
    """
    stored = payload.get("search")
    if not isinstance(stored, dict) or not stored.get("name"):
        return None
    try:
        return parse(
            {section: stored.get(section) or {} for section in _SECTIONS},
            name=str(stored["name"]),
        )
    except SearchDefinitionError:
        return None


def for_run(payload: dict) -> tuple[SearchDefinition | None, str | None]:
    """``(definition, problem)`` for one run's ``lots.json``.

    The named file wins so that re-tuning a requirement re-judges an existing
    run for free. The provenance copy is the fallback, and using it is worth
    saying out loud: the report is then judging against what the search *was*,
    which may not be what the file says now.
    """
    stored = payload.get("search")
    name = stored.get("name") if isinstance(stored, dict) else None
    if not name:
        return None, None

    try:
        return load(str(name)), None
    except SearchDefinitionError as exc:
        if fallback := from_provenance(payload):
            return fallback, (f"{exc} — judged against the copy saved with this "
                              f"run, not the current file")
        return None, str(exc)
