"""Search filter configuration for the bazaraki cars scraper.

`CarFilters` is the single place that describes every input the scraper accepts.
Edit `DEFAULT_FILTERS` (or build your own `CarFilters` and pass it in) to choose
the make/model and the price/year/mileage/etc. ranges to scrape.

How filters map to the site:
  * make / model            -> URL path slugs:  Motors > Cars > Mazda > CX-30
                               => /car-motorbikes-boats-and-parts/cars-trucks-and-vans/mazda/cx-30/
  * price_*, mileage_*       -> raw integer query params (EUR / km)
  * year_*, engine_size_*    -> internal option codes, resolved live from the
                               page's <select> options (see parsers.parse_*_codes)
  * gearbox/fuel/drive/doors -> small, stable label->code maps baked in below
  * body_type/colour/extras  -> raw site option codes (multi-select)
  * seats                    -> actual seat counts (the site code equals the count)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

BASE_URL = "https://www.bazaraki.com"
CARS_PATH = "/car-motorbikes-boats-and-parts/cars-trucks-and-vans/"

# --- Stable enumerated filters: human label (lowercased) -> bazaraki code ---
GEARBOX = {"manual": "2", "automatic": "1", "none": "3"}
FUEL_TYPE = {
    "diesel": "2", "petrol": "7", "plug-in hybrid diesel": "13",
    "hybrid diesel": "30", "plug-in hybrid petrol": "15", "hybrid petrol": "20",
    "lpg": "5", "electric": "10", "none": "11",
}
DRIVE = {"4wd, awd": "10", "front (fwd)": "20", "rear (rwd)": "30"}
DOORS = {"2 - 3 doors": "10", "4 - 5 doors": "20", "6 doors": "30"}


@dataclass
class CarFilters:
    """All available search inputs for the cars category."""

    # --- Category path: Motors > Cars > Make > Model (URL slugs) -----------
    make: str | None = None          # e.g. "mazda" (the slug in the category URL)
    model: str | None = None         # e.g. "cx-30" (requires make)

    # --- Range filters --------------------------------------------------------
    price_min: int | None = None     # EUR
    price_max: int | None = None     # EUR
    year_min: int | None = None      # calendar year, e.g. 2018
    year_max: int | None = None      # calendar year, e.g. 2023
    mileage_min: int | None = None   # km
    mileage_max: int | None = None   # km
    engine_size_min: str | None = None  # label e.g. "1,6L" or "Electric"
    engine_size_max: str | None = None

    # --- Single-choice enumerations (human label, case-insensitive) ----------
    gearbox: str | None = None       # "Automatic" | "Manual" | "None"
    fuel_type: str | None = None     # "Petrol" | "Diesel" | "Hybrid Petrol" | ...
    drive: str | None = None         # "Front (FWD)" | "Rear (RWD)" | "4WD, AWD"
    doors: str | None = None         # "2 - 3 doors" | "4 - 5 doors" | "6 doors"

    # --- Multi-choice (raw site option codes; repeatable) --------------------
    body_type: list[int] = field(default_factory=list)
    colour: list[int] = field(default_factory=list)
    seats: list[int] = field(default_factory=list)   # actual seat counts, 2..9
    extras: list[int] = field(default_factory=list)

    # --- Misc ----------------------------------------------------------------
    q: str | None = None             # free-text query within the category
    condition: str | None = None     # raw site value (e.g. new/used tab)
    ordering: str | None = None      # raw site sort value


# Edit this to change what gets scraped. Mirrors the user's example:
# Motors > Cars > Mazda > CX-30, 2018+, up to €25,000.
DEFAULT_FILTERS = CarFilters(
    make="mazda",
    model="cx-30",
    year_min=2023,
    mileage_min=0,
    mileage_max=50000,
)


def _norm(value: str) -> str:
    return value.strip().lower()


def _lookup(table: dict[str, str], value: str, field_name: str) -> str:
    code = table.get(_norm(value))
    if code is None:
        allowed = ", ".join(sorted({k for k in table}))
        raise ValueError(f"Unknown {field_name} {value!r}. Allowed: {allowed}")
    return code


def base_path(filters: CarFilters) -> str:
    """Category path including optional make/model segments."""
    parts = [CARS_PATH.strip("/")]
    if filters.make:
        parts.append(filters.make.strip("/"))
        if filters.model:
            parts.append(filters.model.strip("/"))
    elif filters.model:
        raise ValueError("model requires make to be set")
    return "/" + "/".join(parts) + "/"


def needs_option_resolution(filters: CarFilters) -> bool:
    """True when a filter is set whose code must be read from the live page."""
    return any(
        v is not None
        for v in (
            filters.year_min, filters.year_max,
            filters.engine_size_min, filters.engine_size_max,
        )
    )


def build_query(
    filters: CarFilters,
    *,
    year_codes: dict[int, str] | None = None,
    engine_codes: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Build the ordered (key, value) query pairs for the search URL.

    `year_codes` / `engine_codes` map human values to the site's option codes
    and are required only when the corresponding filters are set
    (see parsers.parse_year_codes / parse_engine_codes).
    """
    params: list[tuple[str, str]] = []

    def add(key: str, value) -> None:
        if value is not None and value != "":
            params.append((key, str(value)))

    add("price_min", filters.price_min)
    add("price_max", filters.price_max)
    add("attrs__mileage_min", filters.mileage_min)
    add("attrs__mileage_max", filters.mileage_max)

    for year, key in ((filters.year_min, "attrs__year_min"), (filters.year_max, "attrs__year_max")):
        if year is not None:
            if not year_codes or year not in year_codes:
                raise ValueError(f"No site code for year {year}; resolve options first")
            add(key, year_codes[year])

    for size, key in (
        (filters.engine_size_min, "attrs__engine-size_min"),
        (filters.engine_size_max, "attrs__engine-size_max"),
    ):
        if size is not None:
            code = (engine_codes or {}).get(_norm(size))
            if code is None:
                raise ValueError(f"No site code for engine size {size!r}; resolve options first")
            add(key, code)

    if filters.gearbox:
        add("attrs__gearbox", _lookup(GEARBOX, filters.gearbox, "gearbox"))
    if filters.fuel_type:
        add("attrs__fuel-type", _lookup(FUEL_TYPE, filters.fuel_type, "fuel_type"))
    if filters.drive:
        add("attrs__drive", _lookup(DRIVE, filters.drive, "drive"))
    if filters.doors:
        add("attrs__doors", _lookup(DOORS, filters.doors, "doors"))

    for code in filters.body_type:
        add("attrs__body-type", code)
    for code in filters.colour:
        add("attrs__colour", code)
    for code in filters.seats:
        add("attrs__seats", code)
    for code in filters.extras:
        add("attrs__extras", code)

    add("q", filters.q)
    add("condition", filters.condition)
    add("ordering", filters.ordering)
    return params


def build_search_url(
    filters: CarFilters,
    *,
    year_codes: dict[int, str] | None = None,
    engine_codes: dict[str, str] | None = None,
) -> str:
    """Absolute search URL for the given filters."""
    url = BASE_URL + base_path(filters)
    query = build_query(filters, year_codes=year_codes, engine_codes=engine_codes)
    if query:
        url += "?" + urlencode(query)
    return url