"""Turn banzai24's API JSON into flat, typed rows.

This is the boundary between banzai24's schema and ours. Everything downstream —
the database, the CSV, the report, the bid logic — reads the row this module
produces, so the site's inconsistencies are absorbed once, here, instead of
being rediscovered by each consumer.

What actually needs absorbing, all of it observed in saved runs:

* **Prices are strings, in two formats.** ``"1 180 000 ¥"`` and a bare
  ``"980000"`` both occur, for the same field.
* **Half the vocabulary is Russian UI text** — ``"Автомат"``, ``"Гибрид"``,
  ``status.name = "Продан"``. The *codes* (``status.code``, ``color``) are
  stable; the names are a rendering, and are not stored.
* **The same fact appears in several places.** The registration month is a
  top-level ``registrationMonth`` on some lots and only inside
  ``car.year = "2023.08"`` on others; the chassis code is spread across three
  fields written to two different conventions.
* **Empty string means absent.** ``drivetrain``, ``steeringWheelSide`` and
  ``fuelType`` all come back as ``""`` rather than null.

Convention for the values that come out: codes keep the API's spelling
(``status_code`` stays ``"SOLD"``, matching :data:`banzai24.fetch.CONCLUDED_STATUSES`),
while values we translate out of Russian become lower-case English words
(``transmission = "auto"``, ``steering = "right"``, ``colour = "white"``).

:func:`normalize_lot` is pure — a dict in, a dict out, no filesystem. Attaching
the downloaded sheet is :func:`attach_sheet`'s job, so the parsing half stays
testable against a fixture with nothing on disk.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import db, export
from .lot_filters import model_code_of

# --- scalars -----------------------------------------------------------------

# The Gregorian year of era year 0, so era year N is OFFSET + N. Reiwa 1 = 2019,
# Heisei 1 = 1989, Showa 1 = 1926. Both the kanji and the single-letter
# abbreviation are accepted because auction sheets use either.
_ERA_OFFSETS = {
    "令和": 2018, "R": 2018,
    "平成": 1988, "H": 1988,
    "昭和": 1925, "S": 1925,
    "大正": 1911, "T": 1911,
    "明治": 1867, "M": 1867,
}

# "R5年1月", "令和5年1月", "R5/1", "H31.12" — era, year, then month. 元 (gannen)
# is the written form of year 1 and appears instead of a digit.
_ERA_DATE = re.compile(
    r"^\s*(令和|平成|昭和|大正|明治|[RHSTM])\s*(\d{1,2}|元)\s*(?:年)?\s*"
    r"[./\-]?\s*(\d{1,2})?\s*(?:月)?\s*$"
)


def parse_era_date(text: str | None) -> tuple[int, int] | None:
    """``"R5年1月"`` -> ``(2023, 1)``. Japanese era year to Gregorian.

    Auction sheets print registration dates in era years while the API reports
    Gregorian ones, so the two are only comparable after this. Returns ``None``
    for anything unparseable — a misread sheet should leave the field empty, not
    invent a year.

    The month is optional (``"R5年"`` -> ``(2023, 0)``) because a sheet
    sometimes prints only the year.
    """
    if not text:
        return None
    match = _ERA_DATE.match(str(text))
    if not match:
        return None
    era, era_year, month = match.groups()
    offset = _ERA_OFFSETS.get(era)
    if offset is None:
        return None
    year = 1 if era_year == "元" else int(era_year)
    if year < 1:
        return None
    return offset + year, int(month) if month else 0


def parse_price(value: object) -> int | None:
    """``"1 180 000 ¥"`` and ``"980000"`` both -> ``1180000`` / ``980000``.

    Every non-digit is dropped, so the thousands separator and the currency
    glyph are handled without caring which of the two formats this lot used.

    **Zero reads as absent.** banzai24 writes ``"0 ¥"`` for a lot that has no
    sale price yet, and storing that as ``0`` would poison every average and
    minimum computed over the column later.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    return int(digits) or None


def blank_to_none(value: object) -> str | None:
    """``""`` and whitespace read as absent, which is what the API means by them."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Russian UI text -> the words we store. Anything unrecognised is kept verbatim
# rather than dropped: a gearbox or fuel type we have not seen should show up in
# the data looking odd, not vanish. That is also why neither table guesses at
# "Газ", which could be LPG or CNG.
_TRANSMISSIONS = {
    "автомат": "auto",
    "механика": "manual",
    "механическая": "manual",
    "робот": "robot",
    "вариатор": "cvt",
}

_FUEL_TYPES = {
    "бензин": "petrol",
    "дизель": "diesel",
    "гибрид": "hybrid",
    "электро": "electric",
    "электрический": "electric",
}


def _translate(value: str | None, table: dict[str, str]) -> str | None:
    if (text := blank_to_none(value)) is None:
        return None
    return table.get(text.lower(), text)


def normalize_transmission(value: str | None) -> str | None:
    return _translate(value, _TRANSMISSIONS)


def normalize_fuel_type(value: str | None) -> str | None:
    """``"Гибрид"`` -> ``"hybrid"``; ``""`` -> ``None``.

    Absent on most lots — 72 of the 112 in the saved runs leave it blank — and
    there is no recovering it from elsewhere. ``characteristics.engine`` looks
    like a second source (``"2.5 л / Гибрид"``) but it names the fuel in exactly
    the cases ``fuelType`` already does, so a fallback to it would gain nothing.
    A blank stays blank rather than being inferred from the engine size.
    """
    return _translate(value, _FUEL_TYPES)


def normalize_steering(value: str | None) -> str | None:
    """``"RIGHT"`` -> ``"right"``; ``""`` -> ``None`` (11 of 112 saved lots)."""
    if (text := blank_to_none(value)) is None:
        return None
    return text.lower()


def parse_registration(item: dict) -> tuple[int | None, int | None]:
    """``(year, month)`` of first registration, from wherever the lot carries it.

    ``registrationYear`` is always present but ``registrationMonth`` is null on
    most lots (71 of 112 saved), while ``car.year`` carries ``"2023.08"`` — the
    same fact, one field over. Neither source alone is enough.
    """
    year = item.get("registrationYear")
    month = item.get("registrationMonth")

    car_year = blank_to_none((item.get("car") or {}).get("year")) or blank_to_none(
        (item.get("characteristics") or {}).get("year")
    )
    if car_year:
        head, _, tail = car_year.partition(".")
        if year is None and head.isdigit():
            year = int(head)
        if month is None and tail.isdigit():
            month = int(tail)

    year = int(year) if year is not None else None
    month = int(month) if month is not None else None
    if month is not None and not 1 <= month <= 12:
        month = None
    return year, month


# --- one lot -----------------------------------------------------------------


def normalize_lot(item: dict) -> dict:
    """One API item -> one flat row, keyed like :class:`banzai24.models.AuctionLot`.

    Pure: no filesystem, no clock, no database. The sheet columns
    (``sheet_path``, ``sheet_sha256``, ``sheet_status``) are left for
    :func:`attach_sheet`, which needs the run directory.

    Raises :class:`ValueError` if the lot has no ``lot.number``. That is the
    primary key, and a lot without one cannot be stored, deduplicated or
    re-found — better to fail on it than to write a row nothing can address.
    """
    lot = item.get("lot") or {}
    car = item.get("car") or {}
    characteristics = item.get("characteristics") or {}
    auction = lot.get("auction") or {}

    lot_number = blank_to_none(lot.get("number"))
    if not lot_number:
        raise ValueError(f"lot has no lot.number: {json.dumps(item)[:200]}")

    trade_date = blank_to_none(lot.get("tradeDate"))
    registration_year, registration_month = parse_registration(item)

    return {
        "lot_number": lot_number,
        "lot_short": blank_to_none(lot.get("shortNumber")) or lot_number,
        "banzai_id": blank_to_none(item.get("id")) or "",
        "auction_id": int(auction.get("id") or 0),
        "auction_name": blank_to_none(auction.get("name")) or "",
        "trade_date": date.fromisoformat(trade_date) if trade_date else None,
        "trade_time": blank_to_none(lot.get("tradeTime")) or "",

        "mark": blank_to_none(car.get("mark")),
        "model": blank_to_none(car.get("model")),
        "model_id": int(car["modelId"]) if car.get("modelId") is not None else None,
        "modification": blank_to_none(characteristics.get("modification")),
        # The three-field, two-convention fallback lives in lot_filters, where
        # the same normalisation decides what a --body-model-code filter matches.
        # Storing anything else would let the stored value disagree with the
        # filter that selected the lot.
        "body_model_code": blank_to_none(model_code_of(item)),
        "body_number": blank_to_none(characteristics.get("bodyNumber")),
        "registration_year": registration_year,
        "registration_month": registration_month,
        "mileage_km": int(m) if (m := characteristics.get("mileage")) is not None else None,
        "engine_capacity": blank_to_none(characteristics.get("engineCapacity")),
        "fuel_type": normalize_fuel_type(characteristics.get("fuelType")),
        "transmission": normalize_transmission(characteristics.get("transmission")),
        "colour": (c.lower() if (c := blank_to_none(characteristics.get("color"))) else None),
        "steering": normalize_steering(characteristics.get("steeringWheelSide")),

        "grade_origin": blank_to_none(item.get("gradeOrigin")) or blank_to_none(item.get("grade")),
        "status_code": (s.upper() if (s := blank_to_none((item.get("status") or {}).get("code"))) else None),
        "start_price_jpy": parse_price(item.get("startPrice")),
        "end_price_jpy": parse_price(item.get("endPrice")),
        "currency": blank_to_none(item.get("currency")),
        "source": blank_to_none(item.get("source")),

        "sheet_url": blank_to_none(item.get("auctImage")),
    }


def normalize_lots(items: list[dict]) -> tuple[list[dict], list[str]]:
    """``(rows, problems)`` — normalise many lots without one bad item stopping the rest.

    A lot the API returned in a shape we cannot read is a fact about the run,
    not a reason to lose the other nineteen, so it is reported rather than
    raised.
    """
    rows, problems = [], []
    for item in items:
        try:
            rows.append(normalize_lot(item))
        except Exception as exc:
            problems.append(str(exc))
    return rows, problems


# --- the sheet on disk -------------------------------------------------------


PROJECT_ROOT = Path(__file__).parent.parent


def _portable_path(path: Path) -> str:
    """Store a sheet path relative to the project root where possible.

    An absolute path would be baked into the database and break the moment the
    checkout moves or the run directory is copied to another machine — which
    Phase 4 explicitly expects to happen.
    """
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def attach_sheet(row: dict, run_dir: Path) -> dict:
    """Fill in ``sheet_path`` / ``sheet_sha256`` / ``sheet_status`` from disk.

    Kept out of :func:`normalize_lot` so that stays pure. The hash is what Phase
    3 deduplicates paid extractions on, so it is computed here, once, at the
    point the file is known to exist.

    ``no_sheet`` and a missing download are deliberately different: the first
    means banzai24 published no sheet for this lot and never will, the second
    means ``fetch --no-sheets`` ran or the download failed and re-running would
    help.
    """
    # Imported here rather than at module scope: fetch pulls in playwright, and
    # normalising a saved run has no business requiring a browser to be installed.
    from .fetch import sha256_of, sheet_filename

    row = dict(row)
    if not row.get("sheet_url"):
        row.update(sheet_path=None, sheet_sha256=None, sheet_status="no_sheet")
        return row

    path = run_dir / "sheets" / sheet_filename({"lot": {"number": row["lot_number"]}})
    if not path.exists():
        row.update(sheet_path=None, sheet_sha256=None, sheet_status="pending")
        return row

    row.update(
        sheet_path=_portable_path(path),
        sheet_sha256=sha256_of(path),
        sheet_status="pending",
    )
    return row


# --- a whole saved run -------------------------------------------------------


def _selected_numbers(payload: dict) -> set[str] | None:
    """The lot numbers this run kept, or ``None`` if the file predates the field."""
    selected = payload.get("lots_selected")
    if selected is None:
        return None
    return {number for number in selected if number}


def load_run(
    run_dir: Path, all_lots: bool = False
) -> tuple[list[dict], list[str]]:
    """Read ``<run>/lots.json`` and normalise it. ``(rows, problems)``.

    By default this covers **the lots the run kept** — the ones the day
    narrowing and the filters selected, which are the ones with sheets on disk
    and the ones the report is about. ``all_lots`` widens it to every lot the
    fetch saw, including the later auction days it set aside; those are real
    market data and cost nothing extra now that they are saved.

    Runs written before ``lots_selected`` existed have no selection recorded, so
    they normalise in full whatever this flag says.
    """
    payload = json.loads((run_dir / "lots.json").read_text(encoding="utf-8"))
    items = [item for page in payload.get("pages", []) for item in (page.get("items") or [])]

    selected = _selected_numbers(payload)
    if not all_lots and selected is not None:
        items = [
            item for item in items
            if ((item.get("lot") or {}).get("number") or "") in selected
        ]

    rows, problems = normalize_lots(items)
    return [attach_sheet(row, run_dir) for row in rows], problems


def latest_run(root: Path | None = None) -> Path | None:
    """The most recent run directory. Run names start with a timestamp, so
    lexical order is chronological order."""
    root = root or PROJECT_ROOT / "runs"
    runs = [d for d in root.glob("*") if (d / "lots.json").exists()]
    return max(runs, key=lambda d: d.name) if runs else None


@dataclass
class NormalizeResult:
    run_dir: Path
    rows: list[dict]
    inserted: int = 0
    updated: int = 0
    csv_path: Path | None = None
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"{len(self.rows)} lots normalized"]
        if self.inserted or self.updated:
            bits.append(f"{self.inserted} new, {self.updated} updated in {db.DB_PATH.name}")
        if self.csv_path:
            bits.append(f"csv -> {self.csv_path}")
        if self.problems:
            bits.append(f"{len(self.problems)} unreadable")
        return ", ".join(bits)


def run_normalize(
    run_dir: Path,
    all_lots: bool = False,
    to_db: bool = True,
    to_csv: bool = True,
) -> NormalizeResult:
    """Normalise a saved run into the database and ``lots.csv``.

    Touches nothing but the run directory and the database — no network, no
    browser, no session. That is the point of the split: a bug in a parser here
    is fixed by re-running this over every saved run, not by re-fetching them.
    """
    rows, problems = load_run(run_dir, all_lots=all_lots)
    result = NormalizeResult(run_dir=run_dir, rows=rows, problems=problems)

    if to_db and rows:
        db.init_db()
        result.inserted, result.updated = db.upsert_lots(rows)
    if to_csv:
        result.csv_path = export.write_lots_csv(rows, run_dir / "lots.csv")
    return result
