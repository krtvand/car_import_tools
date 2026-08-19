"""The number you type into the bidding platform, and why it is sometimes blank.

Three names, used verbatim everywhere below and on the report:

* **``max_bid``** — the all-in maximum for that car, read off
  ``inputs/bid_prices.csv``. Operator-authored, re-tuned often, in JPY because
  that is the currency you bid in.
* **``extra_costs``** — the auction house's ``AREA PRICE JPY``, read off
  ``inputs/auction_area_prices_2026.csv``. The only thing subtracted.
* **``bid_reduced``** — ``max_bid − extra_costs``. This is the price to enter.

Two CSVs rather than code or a database: the lists are already spreadsheet-shaped,
they diff readably in git, and re-tuning one must never mean editing Python. The
lookup here is pure — no network, no database — so :mod:`banzai24.report` stays
free to regenerate.

**Nothing about one lot can stop the report.** Every way a lot can fail to price
resolves to a :class:`BidQuote` carrying a ``reason`` string instead of a number,
and the card prints the reason where the number would go. The closed set, first
match wins:

1. ``bid prices not loaded`` — the file is absent or unreadable (report-wide)
2. ``area prices not loaded`` — likewise (report-wide)
3. ``unknown auction house: U Tokyo`` — no alias and no fold match
4. ``missing year`` / ``missing mileage`` — neither the sheet nor the API has it
5. ``no table row for MAZDA CX-30 2023 · 15,000 km · private``
6. ``area cost ¥47,000 exceeds max bid ¥30,000``

Reasons 3–6 sit on the card; 1 and 2 are report-wide and print once in the
header, because repeating "bid prices not loaded" sixty-two times is noise.

**The sheet outranks the API** for year and mileage — see
``docs/adr/0001-sheet-outranks-api.md``. The API rounds mileage to the nearest
1,000, so a car whose sheet reads 50,415 km used to be priced from the
*under-50,000* band, ¥150,000 too high, while the report's own requirement check
judged it on the exact figure. One number on a card cannot be read off the sheet
and the one below it off a rounded copy.

A guessed ``bid_reduced`` is worse than no ``bid_reduced``: it is the one number
on the page that spends money. So nothing here falls back to a neighbouring year
or a house that merely looks similar. The **one** thing it does assume is 車歴:
an unreadable box is priced as ``private``, said out loud on the card. That is
the dearer of the two rows (CX-5: ¥2,101,000 private against ¥1,995,000 rental),
so it is not the cautious choice — it is the only one that always resolves, since
some cars have no rental row at all and a rental default would blank their bid
entirely.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

INPUTS_DIR = Path(__file__).parent / "inputs"
BID_PRICES_PATH = INPUTS_DIR / "bid_prices.csv"
AREA_PRICES_PATH = INPUTS_DIR / "auction_area_prices_2026.csv"
ALIASES_PATH = INPUTS_DIR / "auction_aliases.csv"

BID_PRICE_HEADER = ("make", "model", "year", "mileage_min", "mileage_max",
                    "rental", "max_bid_jpy")
AREA_PRICE_HEADER = ("AUCTION NAME", "AREA PRICE $", "AREA PRICE JPY")
ALIAS_HEADER = ("db_name", "area_price_name")

# What the ``rental`` column may say. A blank cell is a load-time error rather
# than a wildcard: under the never-guess rule a lot whose sheet says neither
# never consults the table at all, so a blank row could never match anything and
# is therefore a typo.
RENTAL_KINDS = ("rental", "private")

# The year is not computed from the clock. A path like
# f"auction_area_prices_{date.today().year}.csv" silently loses every
# bid_reduced on 1 January, which is a wrong report that still renders.


class BidTableError(ValueError):
    """A table is on disk but cannot be trusted — a bad band, a duplicate row.

    Raised by the loaders so a test can assert on the edit that caused it.
    :class:`BidPricer` catches it and degrades to "not loaded" with the detail
    attached, so a mis-edited CSV costs you the bid column and not the report.
    """


def _fold(value: str | None) -> str:
    """Case- and punctuation-insensitive key.

    The same fold :func:`bazaraki.analysis._normalise` uses for make/model, applied
    here to auction-house names too — which on its own reconciles ``BAY AUC`` with
    the cost file's ``BAYAUC``. The six houses it cannot reconcile live in
    ``auction_aliases.csv``.
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# --- reading the tables ------------------------------------------------------


def _rows(path: Path, header: tuple[str, ...]):
    """``(line_number, row_dict)`` for each data row, title lines skipped.

    Everything above the header is dropped, because a Numbers export writes the
    sheet's own name on line 1 and hand-deleting it before every re-export is the
    kind of step that gets forgotten once and then produces a report with no
    prices on it.

    The header is matched folded — ``Auction Name`` is ``AUCTION NAME`` — and the
    rows come back keyed by the **canonical** names in ``header`` rather than by
    whatever the file spelled. Keying by the file's own spelling would mean a
    re-export that recased the header still passed this check and then missed
    every ``row["AUCTION NAME"]`` below it: 123 rows silently read as zero, which
    is the wrong-report-that-still-renders this module exists to avoid.

    A column *order* the header does not match is a different matter, and raises.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        fields: list[str] | None = None
        wanted = tuple(_fold(name) for name in header)
        for line_no, raw in enumerate(csv.reader(handle), start=1):
            cells = [cell.strip() for cell in raw]
            if not any(cells):
                continue
            if fields is None:
                if tuple(_fold(cell) for cell in cells[:len(header)]) == wanted:
                    fields = list(header)
                continue
            yield line_no, dict(zip(fields, cells))
        if fields is None:
            raise BidTableError(f"{path.name}: no header row ({','.join(header)})")


def _int(value: str | None, path: Path, line: int, column: str,
         blank: int | None = ...) -> int | None:
    """One integer cell. ``blank`` is what an empty cell means, if anything."""
    text = (value or "").replace(",", "").replace("¥", "").replace("$", "").strip()
    if not text:
        if blank is ...:
            raise BidTableError(f"{path.name} line {line}: {column} is empty")
        return blank
    try:
        return int(text)
    except ValueError:
        raise BidTableError(
            f"{path.name} line {line}: {column} is not a whole number ({value!r})"
        ) from None


@dataclass(frozen=True)
class BidRow:
    """One row of ``bid_prices.csv``.

    Mileage bands are **inclusive at both ends** and blank means open-ended, so
    ``,50000`` is "up to and including 50,000 km" and ``60001,`` is "60,001 km
    and up". The year is an **exact match** — an unpriced year returns null
    rather than borrowing the number next door.
    """

    make: str
    model: str
    year: int
    mileage_min: int
    mileage_max: int | None    # None = open-ended
    rental: str
    max_bid_jpy: int
    line: int                  # for the overlap error, which names both rows

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (_fold(self.make), _fold(self.model), self.year, self.rental)

    def covers(self, mileage_km: int) -> bool:
        if mileage_km < self.mileage_min:
            return False
        return self.mileage_max is None or mileage_km <= self.mileage_max

    def band(self) -> str:
        top = f"{self.mileage_max:,}" if self.mileage_max is not None else "∞"
        return f"{self.mileage_min:,}–{top} km"


def load_bid_prices(path: Path = BID_PRICES_PATH) -> list[BidRow]:
    """Read ``bid_prices.csv``. Raises :class:`BidTableError` on a bad edit."""
    rows = []
    for line, row in _rows(path, BID_PRICE_HEADER):
        make = row.get("make", "").strip()
        model = row.get("model", "").strip()
        if not make or not model:
            raise BidTableError(f"{path.name} line {line}: make and model are required")

        rental = row.get("rental", "").strip().lower()
        if rental not in RENTAL_KINDS:
            raise BidTableError(
                f"{path.name} line {line}: rental must be one of "
                f"{' or '.join(RENTAL_KINDS)}, not {row.get('rental', '')!r}"
            )

        low = _int(row.get("mileage_min"), path, line, "mileage_min", blank=0)
        high = _int(row.get("mileage_max"), path, line, "mileage_max", blank=None)
        if high is not None and high < low:
            raise BidTableError(
                f"{path.name} line {line}: mileage_max {high:,} is below "
                f"mileage_min {low:,}"
            )

        rows.append(BidRow(
            make=make,
            model=model,
            year=_int(row.get("year"), path, line, "year"),
            mileage_min=low,
            mileage_max=high,
            rental=rental,
            max_bid_jpy=_int(row.get("max_bid_jpy"), path, line, "max_bid_jpy"),
            line=line,
        ))

    _reject_overlaps(rows, path)
    return rows


def _reject_overlaps(rows: list[BidRow], path: Path) -> None:
    """Two rows that could both match one car are an error, at load.

    Checked as *band overlap* rather than "two rows matched this lot", because at
    load time there is no lot — and because the stronger check catches a shadowed
    row today rather than on the morning a car finally falls in the gap. Rows
    differing only in ``rental`` never conflict; they cannot both match.
    """
    groups: dict[tuple, list[BidRow]] = {}
    for row in rows:
        groups.setdefault(row.key, []).append(row)

    for group in groups.values():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                low = max(first.mileage_min, second.mileage_min)
                high = min(
                    float("inf") if first.mileage_max is None else first.mileage_max,
                    float("inf") if second.mileage_max is None else second.mileage_max,
                )
                if low <= high:
                    raise BidTableError(
                        f"{path.name} lines {first.line} and {second.line}: "
                        f"{first.make} {first.model} {first.year} {first.rental} "
                        f"bands overlap ({first.band()} and {second.band()})"
                    )


def load_area_prices(path: Path = AREA_PRICES_PATH) -> dict[str, int]:
    """``{folded house name: AREA PRICE JPY}``.

    The ``AREA PRICE $`` column is deliberately ignored. The two columns are not
    one conversion of the other (35→4,000 is ¥114/$, 365→47,000 is ¥129/$) — they
    are two separately quoted prices, and you bid in yen.
    """
    prices: dict[str, int] = {}
    seen: dict[str, int] = {}
    for line, row in _rows(path, AREA_PRICE_HEADER):
        name = row.get("AUCTION NAME", "").strip()
        if not name:
            continue
        key = _fold(name)
        if key in seen:
            raise BidTableError(
                f"{path.name} lines {seen[key]} and {line}: {name} appears twice"
            )
        seen[key] = line
        prices[key] = _int(row.get("AREA PRICE JPY"), path, line, "AREA PRICE JPY")
    return prices


def load_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    """``{folded auction.db name: folded cost-file name}``.

    Only the houses whose two spellings the fold cannot reconcile — six today.
    An alias pointing at a name the cost file does not have is not an error: it
    lands as "unknown auction house" on the card, which is the same place a new
    house lands, and neither should stop a report.
    """
    aliases: dict[str, str] = {}
    seen: dict[str, int] = {}
    for line, row in _rows(path, ALIAS_HEADER):
        db_name = row.get("db_name", "").strip()
        target = row.get("area_price_name", "").strip()
        if not db_name or not target:
            raise BidTableError(
                f"{path.name} line {line}: both db_name and area_price_name are required"
            )
        key = _fold(db_name)
        if key in seen:
            raise BidTableError(
                f"{path.name} lines {seen[key]} and {line}: {db_name} is aliased twice"
            )
        seen[key] = line
        aliases[key] = _fold(target)
    return aliases


# --- one lot's answer --------------------------------------------------------


@dataclass(frozen=True)
class BidQuote:
    """What to bid on one lot, or why there is no number.

    ``bid_reduced`` and ``reason`` are mutually exclusive: exactly one of them is
    set. ``max_bid`` and ``extra_costs`` are shown whenever they are known, even
    when the other half is missing — knowing the house costs ¥12,000 is useful on
    a card that cannot price the car, and vice versa.
    """

    max_bid: int | None = None
    extra_costs: int | None = None
    bid_reduced: int | None = None
    reason: str | None = None
    house: str | None = None      # the lot's own display name, for the label
    assumed_private: bool = False  # the sheet did not say; priced as private

    def lines(self) -> list[str]:
        """The money block, one string per line."""
        assumed = " — assuming private, sheet did not say" if self.assumed_private else ""
        parts = [f"max bid ¥{self.max_bid:,}{assumed}" if self.max_bid is not None
                 else "max bid —"]
        label = f" ({self.house})" if self.house else ""
        parts.append(f"area{label} −¥{self.extra_costs:,}" if self.extra_costs is not None
                     else f"area{label} —")
        parts.append(f"bid reduced ¥{self.bid_reduced:,}" if self.bid_reduced is not None
                     else (self.reason or "no bid price"))
        return parts

    def describe(self) -> str:
        return " · ".join(self.lines())


def _rental_kind(extraction) -> tuple[str, bool]:
    """``(kind, assumed)`` — ``"private"`` when the sheet did not say.

    The distinction exists only on the auction sheet, so an unread sheet has no
    answer, and neither does a company car whose 車歴 box says neither. Both are
    priced as private and flagged as an assumption rather than left blank: a card
    with no max bid on it is a card you have to price by hand, and the number is
    sitting right there in the table.

    ``assumed`` is not cosmetic. It is the difference between a figure the sheet
    supports and one that is only the more common case, and the card prints it
    next to the money for exactly that reason.
    """
    if extraction is not None:
        if extraction.rental_car_note:
            return "rental", False
        if extraction.private_car_note:
            return "private", False
    return "private", True


def _load(label, path, loader, empty, quiet_when_absent=False):
    """``(table, problem)`` — reads one file without ever raising.

    This is the whole "a bad table costs the bid column, not the report" rule, in
    one place. The loaders raise so a test can assert on the edit that broke
    them; this turns that into a sentence for the header.
    """
    try:
        return loader(path), None
    except FileNotFoundError:
        return empty, None if quiet_when_absent else f"{label} not loaded"
    except (BidTableError, OSError, UnicodeDecodeError) as exc:
        return empty, f"{label} not loaded: {exc}"


class BidPricer:
    """Both tables, loaded once per report; one pure lookup per lot.

    Mirrors :class:`banzai24.report.CyprusPricer`: a missing input is reported,
    not raised, because a report without the bid column is still the sheet next
    to the fields. Unlike the Cyprus join, a *malformed* table is also reported
    rather than raised — but with the parser's complaint attached, so the edit
    that broke it is named on the page instead of being silently dropped.
    """

    def __init__(
        self,
        bid_prices_path: Path | None = None,
        area_prices_path: Path | None = None,
        aliases_path: Path | None = None,
    ):
        self.rows, bid_problem = _load(
            "bid prices", bid_prices_path or BID_PRICES_PATH, load_bid_prices, [])
        self.area_prices, area_problem = _load(
            "area prices", area_prices_path or AREA_PRICES_PATH, load_area_prices, {})
        # The aliases are an accelerator, not an input: without them six houses
        # stop matching and say so on their own cards, which is a working report.
        # So an absent alias file is silent and does not switch the pricer off —
        # but a mis-edited one is still said out loud.
        self.aliases, alias_problem = _load(
            "auction aliases", aliases_path or ALIASES_PATH, load_aliases, {},
            quiet_when_absent=True)

        self.available = not (bid_problem or area_problem)
        self.reason = "; ".join(
            problem for problem in (bid_problem, area_problem, alias_problem) if problem
        ) or None

    def for_lot(self, lot, extraction=None) -> BidQuote | None:
        """``None`` only when a table is missing — that reason lives in the header.

        Every other outcome is a :class:`BidQuote`, priced or explained. Nothing
        in here raises: a lot that cannot be priced is a line of prose on a card,
        never a failed report.
        """
        if not self.available:
            return None

        extra_costs, house_reason = self._area_cost(lot)
        max_bid, table_reason, assumed_private = self._max_bid(lot, extraction)
        reason = house_reason or table_reason

        bid_reduced = None
        if reason is None and max_bid is not None and extra_costs is not None:
            remaining = max_bid - extra_costs
            if remaining <= 0:
                # Not a bid of nothing, and not a negative number to type into a
                # platform — it is "do not buy this car at this house", and it
                # should not look like a price.
                reason = (f"area cost ¥{extra_costs:,} exceeds "
                          f"max bid ¥{max_bid:,}")
            else:
                bid_reduced = remaining

        return BidQuote(max_bid=max_bid, extra_costs=extra_costs,
                        bid_reduced=bid_reduced, reason=reason,
                        house=lot.auction_name,
                        assumed_private=assumed_private and max_bid is not None)

    def _area_cost(self, lot) -> tuple[int | None, str | None]:
        key = _fold(lot.auction_name)
        price = self.area_prices.get(self.aliases.get(key, key))
        if price is None:
            return None, f"unknown auction house: {lot.auction_name}"
        return price, None

    def _max_bid(self, lot, extraction) -> tuple[int | None, str | None, bool]:
        """``(max_bid, reason, assumed_private)`` for one car.

        **The sheet wins, the API fills its nulls** — the reverse of what
        ``BID_PRICING_QUESTIONS.md`` Q3/Q14 recorded, and the reversal is the
        subject of ``docs/adr/0001-sheet-outranks-api.md``. The API rounds
        mileage to the nearest 1,000 while :meth:`BidRow.covers` matches to the
        kilometre, so under the old order a car whose sheet read 50,415 km and
        whose API row read 50,000 km was priced from the *under-50,000* band:
        ¥150,000 too high against the shipped table, on a card whose own mileage
        row printed the exact figure.

        A sheet null is not a zero. Where the sheet is silent the API's value is
        used unchanged, and where neither has one the lot is not priced at all.
        """
        rental, assumed_private = _rental_kind(extraction)

        year = (extraction.first_registration_year if extraction else None)
        if year is None:
            year = lot.registration_year
        if year is None:
            return None, "missing year", assumed_private

        mileage = (extraction.sheet_mileage_km if extraction else None)
        if mileage is None:
            mileage = lot.mileage_km
        if mileage is None:
            return None, "missing mileage", assumed_private

        key = (_fold(lot.mark), _fold(lot.model), year, rental)
        for row in self.rows:
            if row.key == key and row.covers(mileage):
                return row.max_bid_jpy, None, assumed_private

        return None, (f"no table row for {lot.mark or '?'} {lot.model or '?'} "
                      f"{year} · {mileage:,} km · {rental}"), assumed_private
