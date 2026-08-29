"""A saved search: one TOML file that is the whole declaration for one car.

``searches/mazda-cx30.toml`` holds everything about one car — what to buy it
for in Japan, what to demand of the auction sheet, and who counts as
competition when it is time to sell it in Cyprus. Both parsers read this file
and nothing else; there is no second table to keep in step with it.

**A search is made of bands.** A ``[[band]]`` is a year, a mileage range, the
chassis codes it prices and the max bid for them — the rows that used to live in
``bid_prices.csv``. The
site's own year and mileage bounds are the *union* of the bands and are never
written by hand, because writing them twice is how they drift: when this
replaced the CSV, the CX-30 fetched to 55,000 km while the table priced to
60,000, and the CX-5 fetched to 70,000 km with no bid price above 60,000.

**This module knows nothing about either website.** It validates what it owns —
the car, the bands, the competitor bounds, the dashboard settings — and hands
``[site]``, ``[api]`` and ``[sheet]`` through as raw tables for
:mod:`banzai24.search` to check against its own field names. It imports no
parser, and no parser's vocabulary appears in a .toml: a URL slug and a site's
spelling of "2,0L" are facts about a website, and they live with the parser
that has to speak to it.

Nothing is inherited from anywhere. A filter that is not in the file is not
applied.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import cars
from .cars import Car

SEARCH_DIR = Path(__file__).parent
SUFFIX = ".toml"

# Sections this module reads. `site`, `api` and `sheet` are passed through
# untouched — their keys belong to banzai24's dataclasses, which this module
# deliberately cannot see.
_SECTIONS = ("site", "api", "sheet", "auction_statistics",
             "dashboard", "competitors", "band")

# Passed through to banzai24 rather than parsed here.
_OPAQUE = ("site", "api", "sheet", "auction_statistics")

# What a `[[band]].max_bid_jpy` table may be keyed by. The distinction only
# exists on the auction sheet; see `PRIVATE` below for why one of them is
# mandatory.
RENTAL_KINDS = ("private", "rental")
PRIVATE = "private"

# Derived from the bands, so writing them in [site] is an error rather than an
# override — an override would be a bound that silently disagrees with the
# prices underneath it, which is the exact drift this file was merged to end.
_DERIVED_SITE_KEYS = ("year_start", "year_end", "mileage_start", "mileage_end")

# The car names these, so [site] must not.
_CAR_SITE_KEYS = ("make", "model")

# Moved out of [api] and onto the bands, and derived back from them here. A code
# is worth a different amount of money — the RAV4's E-Four and 2WD are one year,
# one mileage range and ¥445,000 apart — so the code belongs where the price is.
# Left in [api] it would be a second, search-wide answer to the same question.
_DERIVED_API_KEYS = ("body_model_code",)

_COMPETITOR_BOUND_KEYS = ("year_start", "year_end", "mileage_start", "mileage_end")

# A band's competitor block takes the bounds plus the one filter that differs
# band to band. `exclude_phrases` is not a bound — it is checked against what the
# seller wrote — so it is listed here rather than folded into the four above.
_BAND_COMPETITOR_KEYS = _COMPETITOR_BOUND_KEYS + ("exclude_phrases",)


class SearchDefinitionError(ValueError):
    """The file is on disk but cannot be trusted — an unknown key, a bad band.

    Raised, never warned about. A price table that fails to load costs you a
    column; a search definition that fails to load would mean fetching the wrong
    car, judging it against half a list, or pricing it from a band that overlaps
    another one.
    """


# --- small parsing helpers ---------------------------------------------------


def _known(where: str, label: str, keys, allowed: tuple[str, ...]) -> None:
    """An unrecognised key is an error, never a shrug.

    A misspelled ``millage_end`` that loaded as nothing would be a search
    silently running without its mileage bound — a wrong report that still
    renders, which is the failure this codebase is written against.
    """
    for key in keys:
        if key not in allowed:
            raise SearchDefinitionError(
                f"{where}: {label} has no key {key!r}. "
                f"Known keys: {', '.join(sorted(allowed))}"
            )


def _int(value, where: str, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SearchDefinitionError(f"{where}: {label} must be a whole number, not {value!r}")
    if minimum is not None and value < minimum:
        raise SearchDefinitionError(f"{where}: {label} must be at least {minimum}, not {value}")
    return value


def _number(value, where: str, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SearchDefinitionError(f"{where}: {label} must be a number, not {value!r}")
    return float(value)


def _folded_tuple(value, where: str, label: str) -> tuple[str, ...]:
    """A list of strings, lower-cased and stripped for fold-insensitive matching.

    A bare string where a list belongs is the easy typo, and silently iterating
    it into characters would be a filter that matches nothing.
    """
    if isinstance(value, str):
        raise SearchDefinitionError(f"{where}: {label} must be a list, not a string ({value!r})")
    if (not isinstance(value, (list, tuple))
            or not all(isinstance(item, str) for item in value)):
        raise SearchDefinitionError(f"{where}: {label} must be a list of strings")
    return tuple(item.strip().lower() for item in value if item.strip())


def _code_tuple(value, where: str, label: str) -> tuple[str, ...]:
    """A list of chassis codes, upper-cased — the spelling every side compares in.

    Same shape as :func:`_folded_tuple`, opposite fold: banzai24 writes model
    codes upper-case and :func:`banzai24.lot_filters.normalize_model_code`
    upper-cases whatever it is handed, so upper is what a comparison already
    holds on both sides.
    """
    if isinstance(value, str):
        raise SearchDefinitionError(f"{where}: {label} must be a list, not a string ({value!r})")
    if (not isinstance(value, (list, tuple))
            or not all(isinstance(item, str) for item in value)):
        raise SearchDefinitionError(f"{where}: {label} must be a list of strings")
    return tuple(item.strip().upper() for item in value if item.strip())


def _folded(value, where: str, label: str) -> str:
    if not isinstance(value, str):
        raise SearchDefinitionError(f"{where}: {label} must be a string, not {value!r}")
    return value.strip().lower()


# --- the pieces --------------------------------------------------------------


@dataclass(frozen=True)
class CompetitorBounds:
    """Which Cyprus adverts count as competition for one band.

    Deliberately wider than the band itself: a car with more kilometres on it, or
    two years older, still takes the sale away from you. Bounds are inclusive,
    and an absent bound is an absent key — there is no null in TOML and no need
    for one.

    ``exclude_phrases`` is here as well as in ``[competitors]`` because the trim
    you are not selling against is a fact about *one band*. On the RAV4 the two
    are opposites: the AXAH54 band is the G, so an advert saying "hybrid x"
    undercuts it with a cheaper car and is dropped — while the AXAH52 band **is**
    the X, and those same adverts are the whole market it competes in. Band
    phrases add to the search's; they never replace them.
    """

    year_start: int | None = None
    year_end: int | None = None
    mileage_start: int | None = None
    mileage_end: int | None = None
    exclude_phrases: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        """False when the block declared no *bounds*, which is not "no limits".

        Deliberately the four bounds and not ``exclude_phrases``: a band that
        excluded a phrase and bounded nothing has still said nothing about which
        adverts compete with it, and the dashboard has to say so rather than
        crawl every year of the car.
        """
        return any(getattr(self, key) is not None for key in _COMPETITOR_BOUND_KEYS)

    def covers_year(self, year: int | None) -> bool:
        if year is None:
            return False
        if self.year_start is not None and year < self.year_start:
            return False
        return self.year_end is None or year <= self.year_end

    def covers_mileage(self, mileage_km: int | None) -> bool:
        if mileage_km is None:
            return False
        if self.mileage_start is not None and mileage_km < self.mileage_start:
            return False
        return self.mileage_end is None or mileage_km <= self.mileage_end

    def describe(self) -> str:
        years = _range_label(self.year_start, self.year_end, "", comma=False)
        miles = _range_label(self.mileage_start, self.mileage_end, " km")
        return f"years {years} · {miles}"


def _range_label(low, high, unit: str, comma: bool = True) -> str:
    """``2019–any``, ``0–120,000 km``. Years never take a thousands separator."""
    fmt = "{:,}" if comma else "{}"
    left = fmt.format(low) if low is not None else "any"
    right = fmt.format(high) if high is not None else "any"
    return f"{left}–{right}{unit}"


@dataclass(frozen=True)
class Band:
    """One year, one mileage range, one set of chassis codes — and its max bid.

    Mileage is **inclusive at both ends**, and ``mileage_end`` omitted means
    open-ended. The year is an exact match: an unpriced year gets no bid rather
    than borrowing the number next door, because a guessed bid is the one number
    on the page that spends money.

    ``body_model_code`` is here rather than in ``[api]`` because a code is worth
    a different amount of money. The RAV4 was what showed it: the E-Four AXAH54
    and the 2WD AXAH52 are the same year and the same kilometres and ¥445,000
    apart, so one code per search could only ever price one of them. A band that
    names no code prices every code, which is what every single-variant search
    still says by saying nothing.

    ``auction_statistics`` is this band's override of the search's section, and
    is handed on unread for the same reason that section is — the keys are
    banzai24's. It exists because a chassis code does not always name a trim: the
    2WD AXAH52 is a HYBRID X by construction, while the E-Four AXAH54 is a G, an
    Adventure *or* an X. One search-wide trim line therefore has to either mix
    three grades into the E-Four's benchmark or throw away the 2WD sales whose
    grade nobody bothered to type — the same asymmetry that already put
    ``exclude_phrases`` on ``[band.competitors]``. It narrows the *measurement*
    only: a fetch is one URL for the whole search and never reads it.
    """

    year: int
    body_model_code: tuple[str, ...] = ()
    mileage_start: int = 0
    mileage_end: int | None = None
    max_bid_jpy: dict[str, int] = field(default_factory=dict)
    competitors: CompetitorBounds = field(default_factory=CompetitorBounds)
    auction_statistics: dict = field(default_factory=dict)
    expected_profit_eur: float | None = None

    def covers(self, mileage_km: int) -> bool:
        if mileage_km < self.mileage_start:
            return False
        return self.mileage_end is None or mileage_km <= self.mileage_end

    def prices_code(self, code: str | None) -> bool:
        """Does this band price a car wearing this chassis code?

        **Substring**, the same match ``[api]`` always made, so a deliberately
        short ``AXAH5`` covers both AXAH52 and AXAH54 and a full ``5AA-AXAH54``
        still finds its ``AXAH54``. The caller passes the code as the lot
        carries it; stripping the ``5AA-`` type prefix is banzai24's business
        (:func:`banzai24.lot_filters.normalize_model_code`) and this module has
        no opinion about how a website spells one.

        A band that names codes cannot price a car whose code nobody stated —
        the same rule ``[api]`` applies at the fetch, for the same reason:
        nothing has shown it is the variant being priced, and the variant is
        where the money is.
        """
        if not self.body_model_code:
            return True
        code = (code or "").strip().upper()
        return bool(code) and any(wanted in code for wanted in self.body_model_code)

    def bid(self, rental_kind: str = PRIVATE) -> int:
        """The max bid for a 車歴, falling back to ``private``.

        An unreadable 車歴 box is priced as private — the dearer row, so not the
        cautious choice, but the only one that always resolves: some cars have no
        rental row at all and a rental default would blank their bid entirely.
        """
        return self.max_bid_jpy.get(rental_kind, self.max_bid_jpy[PRIVATE])

    @property
    def label(self) -> str:
        top = f"{self.mileage_end:,}" if self.mileage_end is not None else "∞"
        code = f" · {'/'.join(self.body_model_code)}" if self.body_model_code else ""
        return f"{self.year}{code} · {self.mileage_start:,}–{top} km"

    def overlaps(self, other: "Band") -> bool:
        if self.year != other.year:
            return False
        if not _codes_can_meet(self.body_model_code, other.body_model_code):
            # Two variants of one car, same year, same kilometres, different
            # price: that is the whole point of a code on a band, not a clash.
            return False
        low = max(self.mileage_start, other.mileage_start)
        high = min(
            float("inf") if self.mileage_end is None else self.mileage_end,
            float("inf") if other.mileage_end is None else other.mileage_end,
        )
        return low <= high


def _codes_can_meet(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Could one car's code match both lists? Empty means "any code".

    Substring both ways, because that is how :meth:`Band.prices_code` matches:
    ``AXAH5`` and ``AXAH54`` are not two different variants, they are one band
    shadowing another, and a shadowed band is a price you never see.
    """
    if not left or not right:
        return True
    return any(one in two or two in one for one in left for two in right)


@dataclass(frozen=True)
class CompetitorFilters:
    """What a Cyprus advert must be, beyond the car, to count as competition.

    Common to every band. Values are folded to lower case here and matched
    fold-insensitively against whatever the scraper stored, so the file never has
    to know that bazaraki writes ``Hybrid Petrol`` and ``2,0L``.

    **These are applied in memory, not at the scrape.** The crawl is narrowed
    only by what bazaraki can express cleanly in one request — the car and the
    year/mileage union — and everything here is checked against the stored rows
    afterwards. That is what lets ``fuel_type`` be a list (bazaraki's own filter
    is single-choice), keeps raw site option codes out of this file, and keeps
    the panel correct when these are widened without a re-scrape.
    """

    fuel_type: tuple[str, ...] = ()
    gearbox: str | None = None
    seller_type: str | None = None
    exclude_colours: tuple[str, ...] = ()
    engine_size_start: float | None = None   # litres
    engine_size_end: float | None = None
    # Phrases that, said by the seller, disqualify the advert — the trim you are
    # not selling against ("hybrid x", "x package"). Matched as substrings of
    # the advert's title and description, which is the only place a Cyprus
    # advert ever names a trim: bazaraki has no grade field, and about half the
    # RAV4 adverts up today say nothing about it at all. An advert that says
    # nothing is therefore *kept* — it undercuts you until it is shown not to.
    exclude_phrases: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        return any((self.fuel_type, self.gearbox, self.seller_type,
                    self.exclude_colours, self.exclude_phrases,
                    self.engine_size_start is not None,
                    self.engine_size_end is not None))

    def describe(self) -> str:
        bits = []
        if self.fuel_type:
            bits.append(f"fuel {', '.join(self.fuel_type)}")
        if self.gearbox:
            bits.append(f"gearbox {self.gearbox}")
        if self.seller_type:
            bits.append(f"seller {self.seller_type}")
        if self.exclude_colours:
            bits.append(f"not {', '.join(self.exclude_colours)}")
        if self.exclude_phrases:
            bits.append(f"not saying {', '.join(self.exclude_phrases)}")
        if self.engine_size_start is not None or self.engine_size_end is not None:
            low = self.engine_size_start if self.engine_size_start is not None else "any"
            high = self.engine_size_end if self.engine_size_end is not None else "any"
            bits.append(f"engine {low}–{high}L")
        return ", ".join(bits) or "no filters"


@dataclass(frozen=True)
class DashboardSettings:
    """``[dashboard]``. Absent means enabled, so a new search shows up by itself.

    The default is deliberately the noisy one. A search that quietly stayed off
    the dashboard because its section was mistyped would be indistinguishable
    from a car nobody undercuts, and the second is good news.
    """

    enabled: bool = True
    expected_profit_eur: float | None = None


@dataclass(frozen=True)
class SearchDefinition:
    """One car's whole declaration: bands, requirements, competition, settings."""

    name: str
    car: Car
    bands: tuple[Band, ...] = ()
    competitors: CompetitorFilters = field(default_factory=CompetitorFilters)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)
    sections: dict[str, dict] = field(default_factory=dict)   # site / api / sheet, opaque
    source: Path | None = None      # None when read back from run provenance

    # --- bounds derived from the bands ---------------------------------------

    @property
    def year_start(self) -> int | None:
        return min((band.year for band in self.bands), default=None)

    @property
    def year_end(self) -> int | None:
        return max((band.year for band in self.bands), default=None)

    @property
    def mileage_start(self) -> int | None:
        return min((band.mileage_start for band in self.bands), default=None)

    @property
    def mileage_end(self) -> int | None:
        """``None`` when any band is open-ended, which widens the whole search."""
        if not self.bands:
            return None
        if any(band.mileage_end is None for band in self.bands):
            return None
        return max(band.mileage_end for band in self.bands)

    @property
    def body_model_code(self) -> tuple[str, ...]:
        """The codes a fetch keeps: the union of the bands', in file order.

        Derived for the same reason the year and mileage bounds are — the codes
        worth fetching are the codes some band prices, and written twice they
        drift into a lot that arrives and then cannot be priced.

        **One band naming no code unbounds the whole search.** That band prices
        every variant, so narrowing the fetch to the codes its neighbours happen
        to name would drop the very lots it exists to price.
        """
        seen: dict[str, None] = {}
        for band in self.bands:
            if not band.body_model_code:
                return ()
            seen.update(dict.fromkeys(band.body_model_code))
        return tuple(seen)

    def competitor_scope(self) -> CompetitorBounds:
        """The union of every band's competitor bounds — what to actually crawl.

        One scrape per search, not one per band. The bands' bounds overlap
        heavily, and ``bazaraki.db`` bounds delisting to a single run's scope, so
        N overlapping runs would mean crawling the same adverts N times and then
        reasoning about overlapping delisting windows. Each band filters this
        one result set by its own bounds afterwards, in memory.
        """
        declared = [band.competitors for band in self.bands if band.competitors.declared]
        if not declared:
            return CompetitorBounds()

        def widest(key: str, pick):
            values = [getattr(bounds, key) for bounds in declared]
            # One band with no bound on this axis leaves the axis unbounded for
            # the crawl; narrowing to the others would miss its competitors.
            return None if any(value is None for value in values) else pick(values)

        # Phrases are deliberately not unioned here: this is the *crawl* scope,
        # and the phrases are applied in memory afterwards, per band. Excluding
        # at the crawl would delete an advert from every band's evidence because
        # one band did not want it.
        return CompetitorBounds(
            year_start=widest("year_start", min),
            year_end=widest("year_end", max),
            mileage_start=widest("mileage_start", min),
            mileage_end=widest("mileage_end", max),
        )

    def competitors_for(self, band: Band) -> CompetitorFilters:
        """``[competitors]`` plus the phrases this band excludes on its own.

        Additive, like :meth:`profit_for` is not: a phrase the search excludes is
        excluded everywhere, and a band only ever adds to it. There is no way to
        un-exclude from a band, because a phrase the whole search does not sell
        against is not a phrase one band does.
        """
        if not band.competitors.exclude_phrases:
            return self.competitors
        merged = dict.fromkeys(
            self.competitors.exclude_phrases + band.competitors.exclude_phrases)
        return replace(self.competitors, exclude_phrases=tuple(merged))

    def profit_for(self, band: Band) -> float | None:
        """The profit required from one band — its own, else the search's."""
        if band.expected_profit_eur is not None:
            return band.expected_profit_eur
        return self.dashboard.expected_profit_eur

    def band_for(self, year: int | None, mileage_km: int | None,
                 body_model_code: str | None = None) -> Band | None:
        """The band one car falls in, or ``None``. Bands never overlap; see :func:`parse`.

        ``body_model_code`` is what the lot itself carries. Omitting it is
        answering "no code was stated", which no code-bearing band prices —
        so a search whose bands name codes will say ``None`` rather than pick
        the E-Four's price for a car that might be the 2WD.
        """
        if year is None or mileage_km is None:
            return None
        for band in self.bands:
            if (band.year == year and band.covers(mileage_km)
                    and band.prices_code(body_model_code)):
                return band
        return None

    def describe(self) -> str:
        bits = [f"{self.car} ({self.car.key})",
                f"{len(self.bands)} band{'' if len(self.bands) == 1 else 's'}"]
        if self.bands:
            bits.append(_range_label(self.year_start, self.year_end, "", comma=False))
            bits.append(_range_label(self.mileage_start, self.mileage_end, " km"))
        if not self.dashboard.enabled:
            bits.append("dashboard off")
        return " · ".join(bits)

    def to_payload(self) -> dict:
        """The provenance copy written into a run.

        Recorded, but not what renders: the named file wins on re-render so a
        re-tuned requirement re-judges an old morning for free, and under
        ``docs/adr/0004`` the bids ride along with it. This copy is the audit
        trail — what the run was priced at on the day — and the fallback for a
        run whose search file has since been renamed or deleted.
        """
        return {
            "name": self.name,
            "car": self.car.key,
            **{section: dict(self.sections.get(section) or {}) for section in _OPAQUE},
            "bands": [
                {
                    "year": band.year,
                    "body_model_code": list(band.body_model_code),
                    "mileage_start": band.mileage_start,
                    "mileage_end": band.mileage_end,
                    "max_bid_jpy": dict(band.max_bid_jpy),
                    # Recorded too, so the fallback is a whole search rather than
                    # a priced one with no competition declared. A run whose file
                    # has been renamed still renders a full panel.
                    "competitors": {
                        key: value for key, value
                        in vars(band.competitors).items() if value is not None
                    },
                    **({"auction_statistics": dict(band.auction_statistics)}
                       if band.auction_statistics else {}),
                    "expected_profit_eur": band.expected_profit_eur,
                }
                for band in self.bands
            ],
        }


# --- parsing -----------------------------------------------------------------


def _parse_band_stats(payload, where: str) -> dict:
    """``[band.auction_statistics]``, checked for shape and handed on unread.

    Opaque like the section it overrides, and for the same reason: the keys are
    banzai24's spelling of a website's query parameters, which this module
    deliberately cannot see. :func:`banzai24.search.adapt` rejects an unknown
    one, so a typo here is still an error rather than a band quietly measured
    against every trim line of the car.
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise SearchDefinitionError(
            f"{where}: [band.auction_statistics] must be a table")
    return dict(payload)


def _parse_bounds(payload, where: str, label: str) -> CompetitorBounds:
    if not isinstance(payload, dict):
        raise SearchDefinitionError(f"{where}: {label} must be a table")
    _known(where, label, payload, _BAND_COMPETITOR_KEYS)
    values = {
        key: _int(payload[key], where, f"{label}.{key}", minimum=0)
        for key in _COMPETITOR_BOUND_KEYS
        if key in payload
    }
    if "exclude_phrases" in payload:
        values["exclude_phrases"] = _folded_tuple(
            payload["exclude_phrases"], where, f"{label}.exclude_phrases")
    bounds = CompetitorBounds(**values)
    for low, high, name in (
        (bounds.year_start, bounds.year_end, "year"),
        (bounds.mileage_start, bounds.mileage_end, "mileage"),
    ):
        if low is not None and high is not None and high < low:
            raise SearchDefinitionError(
                f"{where}: {label}.{name}_end ({high:,}) is below {name}_start ({low:,})")
    return bounds


def _parse_max_bid(payload, where: str, label: str) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise SearchDefinitionError(
            f"{where}: {label} must be a table keyed by "
            f"{' or '.join(RENTAL_KINDS)}, e.g. {{ private = 1_805_000 }}")
    _known(where, label, payload, RENTAL_KINDS)
    if PRIVATE not in payload:
        # Not an arbitrary requirement: `Band.bid` falls back to private for an
        # unreadable 車歴, so a band without one has a car it cannot price at all.
        raise SearchDefinitionError(
            f"{where}: {label} must set {PRIVATE!r} — it is what an unreadable "
            f"車歴 is priced as")
    return {
        kind: _int(value, where, f"{label}.{kind}", minimum=1)
        for kind, value in payload.items()
    }


def _parse_band(payload, where: str, index: int) -> Band:
    label = f"[[band]] #{index}"
    if not isinstance(payload, dict):
        raise SearchDefinitionError(f"{where}: {label} must be a table")
    _known(where, label, payload,
           ("year", "body_model_code", "mileage_start", "mileage_end",
            "max_bid_jpy", "competitors", "auction_statistics",
            "expected_profit_eur"))

    if "year" not in payload:
        raise SearchDefinitionError(f"{where}: {label} needs a year")
    if "max_bid_jpy" not in payload:
        raise SearchDefinitionError(f"{where}: {label} needs a max_bid_jpy")

    start = _int(payload.get("mileage_start", 0), where, f"{label}.mileage_start", minimum=0)
    end = (_int(payload["mileage_end"], where, f"{label}.mileage_end", minimum=0)
           if "mileage_end" in payload else None)
    if end is not None and end < start:
        raise SearchDefinitionError(
            f"{where}: {label} mileage_end ({end:,}) is below mileage_start ({start:,})")

    band = Band(
        year=_int(payload["year"], where, f"{label}.year", minimum=1900),
        body_model_code=(
            _code_tuple(payload["body_model_code"], where, f"{label}.body_model_code")
            if "body_model_code" in payload else ()),
        mileage_start=start,
        mileage_end=end,
        max_bid_jpy=_parse_max_bid(payload["max_bid_jpy"], where, f"{label}.max_bid_jpy"),
        competitors=_parse_bounds(payload.get("competitors") or {}, where,
                                  f"{label} [band.competitors]"),
        auction_statistics=_parse_band_stats(
            payload.get("auction_statistics"), f"{where}: {label}"),
        expected_profit_eur=(
            _number(payload["expected_profit_eur"], where, f"{label}.expected_profit_eur")
            if "expected_profit_eur" in payload else None),
    )
    _check_band_covered_by_its_competitors(band, where, label)
    return band


def _check_band_covered_by_its_competitors(band: Band, where: str, label: str) -> None:
    """A band's competitor bounds must contain the band itself.

    Bounds narrower than the band would exclude the very car you are buying from
    its own competitor list — an advert identical to your import would not count
    as competing with it. That is always a typo, and it is invisible on the page:
    it shows up as a shorter list, which reads as good news.
    """
    bounds = band.competitors
    if not bounds.declared:
        return
    if not bounds.covers_year(band.year):
        raise SearchDefinitionError(
            f"{where}: {label} competitors {bounds.describe()} exclude the band's "
            f"own year {band.year}")
    if not bounds.covers_mileage(band.mileage_start):
        raise SearchDefinitionError(
            f"{where}: {label} competitors {bounds.describe()} exclude the band's "
            f"own mileage floor {band.mileage_start:,} km")
    if band.mileage_end is None:
        if bounds.mileage_end is not None:
            raise SearchDefinitionError(
                f"{where}: {label} is open-ended but its competitors stop at "
                f"{bounds.mileage_end:,} km")
    elif not bounds.covers_mileage(band.mileage_end):
        raise SearchDefinitionError(
            f"{where}: {label} competitors {bounds.describe()} exclude the band's "
            f"own mileage ceiling {band.mileage_end:,} km")


def _reject_overlaps(bands: tuple[Band, ...], where: str) -> None:
    """Two bands that could both price one car are an error, at load.

    Checked as band overlap rather than "two bands matched this lot", because at
    load time there is no lot — and because the stronger check catches a shadowed
    band today rather than on the morning a car finally falls in the gap.
    """
    for index, first in enumerate(bands):
        for second in bands[index + 1:]:
            if first.overlaps(second):
                raise SearchDefinitionError(
                    f"{where}: bands {first.label} and {second.label} overlap")


def _parse_competitors(payload, where: str) -> CompetitorFilters:
    if not isinstance(payload, dict):
        raise SearchDefinitionError(f"{where}: [competitors] must be a table")
    allowed = ("fuel_type", "gearbox", "seller_type", "exclude_colours",
               "exclude_phrases", "engine_size_start", "engine_size_end")
    _known(where, "[competitors]", payload, allowed)

    filters = CompetitorFilters(
        fuel_type=(_folded_tuple(payload["fuel_type"], where, "[competitors] fuel_type")
                   if "fuel_type" in payload else ()),
        gearbox=(_folded(payload["gearbox"], where, "[competitors] gearbox")
                 if "gearbox" in payload else None),
        seller_type=(_folded(payload["seller_type"], where, "[competitors] seller_type")
                     if "seller_type" in payload else None),
        exclude_colours=(_folded_tuple(payload["exclude_colours"], where,
                                       "[competitors] exclude_colours")
                         if "exclude_colours" in payload else ()),
        exclude_phrases=(_folded_tuple(payload["exclude_phrases"], where,
                                       "[competitors] exclude_phrases")
                         if "exclude_phrases" in payload else ()),
        engine_size_start=(_number(payload["engine_size_start"], where,
                                   "[competitors] engine_size_start")
                           if "engine_size_start" in payload else None),
        engine_size_end=(_number(payload["engine_size_end"], where,
                                 "[competitors] engine_size_end")
                         if "engine_size_end" in payload else None),
    )
    if (filters.engine_size_start is not None and filters.engine_size_end is not None
            and filters.engine_size_end < filters.engine_size_start):
        raise SearchDefinitionError(
            f"{where}: [competitors] engine_size_end is below engine_size_start")
    return filters


def _parse_dashboard(payload, where: str) -> DashboardSettings:
    if not isinstance(payload, dict):
        raise SearchDefinitionError(f"{where}: [dashboard] must be a table")
    _known(where, "[dashboard]", payload, ("enabled", "expected_profit_eur"))
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SearchDefinitionError(f"{where}: [dashboard] enabled must be true or false")
    return DashboardSettings(
        enabled=enabled,
        expected_profit_eur=(
            _number(payload["expected_profit_eur"], where, "[dashboard] expected_profit_eur")
            if "expected_profit_eur" in payload else None),
    )


def _check_site(site: dict, where: str) -> None:
    """``[site]`` may not name what the bands and the car already say."""
    for key in _DERIVED_SITE_KEYS:
        if key in site:
            raise SearchDefinitionError(
                f"{where}: [site] {key} is derived from the bands and must not be "
                f"set here — edit the [[band]] list instead")
    for key in _CAR_SITE_KEYS:
        if key in site:
            raise SearchDefinitionError(
                f"{where}: [site] {key} is named by `car`, not here")


def _check_api(api: dict, where: str) -> None:
    """``[api]`` may not name what the bands now say."""
    for key in _DERIVED_API_KEYS:
        if key in api:
            raise SearchDefinitionError(
                f"{where}: [api] {key} is a [[band]] key now — a band is what "
                f"prices one code — and the fetch derives it from the bands. "
                f"Write it in each [[band]] instead")


def parse(payload: dict, name: str, path: Path | None = None) -> SearchDefinition:
    """Build a definition from already-decoded TOML (or from run provenance)."""
    where = (path.name if path else f"{name}{SUFFIX}")

    if not isinstance(payload, dict):
        raise SearchDefinitionError(f"{where}: not a table")
    if extra := set(payload) - {"car", *_SECTIONS}:
        raise SearchDefinitionError(
            f"{where}: unknown section(s) {', '.join(sorted(extra))}. "
            f"Known: car, {', '.join(_SECTIONS)}")

    key = payload.get("car")
    if not isinstance(key, str) or not key.strip():
        raise SearchDefinitionError(f"{where}: `car` is required, e.g. car = \"mazda-cx30\"")
    try:
        car = cars.get(key.strip())
    except cars.UnknownCar as exc:
        # KeyError's str() is the *repr* of its argument, quotes and all.
        raise SearchDefinitionError(f"{where}: {exc.args[0]}") from None

    sections = {section: dict(payload.get(section) or {}) for section in _OPAQUE}
    _check_site(sections["site"], where)
    _check_api(sections["api"], where)

    raw_bands = payload.get("band") or []
    if not isinstance(raw_bands, list):
        raise SearchDefinitionError(f"{where}: `band` must be a list of [[band]] tables")
    bands = tuple(_parse_band(entry, where, index)
                  for index, entry in enumerate(raw_bands, start=1))
    if not bands:
        raise SearchDefinitionError(
            f"{where}: needs at least one [[band]] — a search with no band has no "
            f"year or mileage bounds and nothing to bid")
    _reject_overlaps(bands, where)

    return SearchDefinition(
        name=name,
        car=car,
        bands=bands,
        competitors=_parse_competitors(payload.get("competitors") or {}, where),
        dashboard=_parse_dashboard(payload.get("dashboard") or {}, where),
        sections=sections,
        source=path,
    )


# --- finding them on disk ----------------------------------------------------


def path_for(name: str, directory: Path | None = None) -> Path:
    return (directory or SEARCH_DIR) / f"{name}{SUFFIX}"


def available(directory: Path | None = None) -> list[str]:
    """Every saved search's name, alphabetically."""
    directory = directory or SEARCH_DIR
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob(f"*{SUFFIX}"))


def load(name: str, directory: Path | None = None) -> SearchDefinition:
    """Read one saved search by name. Raises if it is absent or malformed."""
    path = path_for(name, directory)
    if not path.exists():
        known = ", ".join(available(directory)) or "none found"
        raise SearchDefinitionError(f"No search named {name!r}. Available: {known}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise SearchDefinitionError(f"{path.name}: {exc}") from None
    return parse(payload, name=name, path=path)


def load_all(directory: Path | None = None) -> list[tuple[str, SearchDefinition | None, str | None]]:
    """``(name, definition, problem)`` for every search on disk.

    One broken file does not hide the others: the dashboard renders every valid
    search and gives the broken one a panel naming the wrong line. A command that
    is about to *spend* money loads by name instead, and lets the error raise.
    """
    out = []
    for name in available(directory):
        try:
            out.append((name, load(name, directory), None))
        except SearchDefinitionError as exc:
            out.append((name, None, str(exc)))
    return out


def from_provenance(payload: dict) -> SearchDefinition | None:
    """The copy a run recorded, or ``None`` if it has none.

    Only reached when the named file has since been renamed or deleted; see
    :meth:`SearchDefinition.to_payload`. Runs fetched before saved searches
    became files recorded nothing and render ungrouped rather than being judged
    against a list nobody declared.
    """
    stored = payload.get("search")
    if not isinstance(stored, dict) or not stored.get("name"):
        return None
    rebuilt = {
        "car": stored.get("car"),
        # Copied, not aliased: the shim below edits this dict, and the payload
        # it came from is the run's own file as somebody else still holds it.
        **{section: dict(stored.get(section) or {}) for section in _OPAQUE},
        "band": [
            {key: value for key, value in band.items() if value is not None}
            for band in (stored.get("bands") or [])
        ],
    }
    _lower_the_old_api_code_onto_the_bands(rebuilt)
    try:
        return parse(rebuilt, name=str(stored["name"]))
    except SearchDefinitionError:
        return None


def _lower_the_old_api_code_onto_the_bands(rebuilt: dict) -> None:
    """Read back a run recorded while ``body_model_code`` was still an ``[api]`` key.

    One code for the whole search *is* the same code on each of its bands, so
    the old shape converts exactly. Dropping the key instead would re-render an
    old morning with the filter switched off — a wider report that still looks
    measured — and refusing to load it would cost the run its fallback entirely.
    """
    codes = (rebuilt.get("api") or {}).pop("body_model_code", None)
    if not codes:
        return
    for band in rebuilt["band"]:
        band.setdefault("body_model_code", list(codes))


def for_run(payload: dict) -> tuple[SearchDefinition | None, str | None]:
    """``(definition, problem)`` for one run's recorded search.

    The named file wins, so re-tuning a requirement — or a max bid — re-judges an
    existing run for free. That the bids move too is deliberate and is the
    subject of ``docs/adr/0004-bid-prices-are-read-live.md``: an old report
    re-rendered shows today's numbers, not the ones you bid on the day.
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
