"""The manufacturer's figures for a car: dimensions, CO₂, Euro standard, fuel.

A **model spec** is true of every car of that model over a span of years; a lot
is one car on one day. Two of these figures spend money — the dimensions decide
shipping volume and so the freight half of a landed cost, and ``co2_gkm`` sets
Cyprus road tax off the CO₂ band table — which is why they are data in
``cars/inputs/model_specs.csv`` rather than constants somewhere.

The table used to live in ``price_calculator/inputs/`` beside the cost book,
and the two are not the same kind of thing. A cost book is what the *world*
charges this month; a model spec is what *Toyota built*, and it changes when a
generation does. Filing the second under the calculator meant that everything
else wanting a car's dimensions — a report, a dashboard — reached into a pricing
package to ask. The arithmetic still lives in :mod:`price_calculator`, which
imports :class:`ModelSpec` from here and holds no figures of its own.

A missing row is ``None``, never a guess: freight is around 17% of the CNF price,
so a car priced off a neighbouring model's dimensions would be wrong by more than
any of the fees the calculator is careful about — and wrong invisibly.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

INPUTS_DIR = Path(__file__).parent / "inputs"
MODEL_SPECS_PATH = INPUTS_DIR / "model_specs.csv"

MODEL_SPEC_HEADER = ("make", "model", "year_from", "year_to", "length_cm",
                     "width_cm", "height_cm", "co2_gkm", "euro_standard",
                     "fuel", "body_model_code")


class ModelSpecError(ValueError):
    """A spec table is on disk but cannot be trusted — a bad year span, a duplicate.

    Raised by the loader so a test can assert on the edit that caused it.
    Callers catch it and degrade to a reason string, so a mis-edited CSV costs
    you the landed cost and not the page.
    """


@dataclass(frozen=True)
class ModelSpec:
    """The manufacturer's figures for one model, over a span of years.

    Dimensions are the first thing here the landed cost reads — they drive
    shipping volume, and freight was 17% of the CNF price on the sheet's own
    reference car, so this is not a rounding input.

    ``co2_gkm`` **is read**, and is the second thing here that spends money:
    road tax comes off the Cyprus CO₂ band table (``price_calculator_spec.md``
    §5), so a row with an empty ``co2_gkm`` cannot be priced at all and every lot
    matching it prints the reason instead of a landed cost. It is not defaulted,
    guessed or interpolated from a neighbouring model — a car's road tax runs
    from €45 to €1,500 a year across the scale, and a guess in the middle of that
    is worth less than a blank.

    ``euro_standard`` and ``fuel`` feed only the one-off registration surcharge,
    which is €0 for every Euro 6 car and so €0 for everything in
    ``model_specs.csv`` today. They are deliberately weaker inputs than
    ``co2_gkm``: an unrecorded ``euro_standard`` is priced at no surcharge and
    said out loud on the answer, and an unrecorded ``fuel`` is priced from the
    diesel column, the dearer of the two on every row.

    ``body_model_code`` is descriptive — a space-separated list of the codes this
    row covers, for a human checking that a lot belongs to this row. It is the
    upgrade path rather than dead weight: it is what separates a hybrid RAV4
    (``AXAH54``) from a petrol one (``MXAA54``), and it separates them *more
    reliably than fuel does* — ``auction.db`` holds the same ``KFEP`` six times
    with a null ``fuel_type`` and four times as ``petrol``.

    Now that road tax *is* a function of CO₂, the key here wants to grow to
    ``(make, model, year, body_model_code)``, because a hybrid and a petrol of
    one generation differ by far more CO₂ than they do centimetres. That is a
    real gap and not a hypothetical one; it is left open because closing it means
    a CO₂ figure per body code for every row, and one wrong band is €50–€100
    against the €25 of freight precision the calculator frets about elsewhere.
    Until then a row's ``co2_gkm`` is the generation's figure and the margin
    inherits that error.

    A **trim** is not in this table and does not want to be. Every Harrier trim
    is the same 4,740 mm box on the same hybrid drivetrain, so a trim splits the
    price by ¥1.5M and the freight by nothing at all. See :mod:`cars.trims`.
    """

    make: str
    model: str
    year_from: int
    year_to: int
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    co2_gkm: int | None = None
    euro_standard: str | None = None
    fuel: str | None = None
    body_model_code: str | None = None
    line: int = 0  # for the overlap error, which names both rows

    @property
    def volume_m3(self) -> Decimal:
        """The shipping box, ``Calculator!B38:B40`` — length × width × height."""
        return (self.length_cm * self.width_cm * self.height_cm) / Decimal(1_000_000)

    def covers(self, year: int) -> bool:
        return self.year_from <= year <= self.year_to

    def describe(self) -> str:
        return (f"{self.make} {self.model} {self.year_from}–{self.year_to} · "
                f"{self.length_cm:.0f}×{self.width_cm:.0f}×{self.height_cm:.0f} cm · "
                f"{self.volume_m3:.2f} m³")


def _fold(value: str | None) -> str:
    """The same fold ``banzai24.bidding``, ``bazaraki.analysis`` and
    ``price_calculator.sources`` use.

    Shared by copy rather than by import because each of theirs is private, and a
    public re-export would make one module the owner of a convention none of them
    owns. It is one regex.
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _rows(path: Path, header: tuple[str, ...]):
    """``(line_number, row_dict)`` per data row, everything above the header dropped.

    Mirrors :func:`banzai24.bidding._rows`, including *why*: the header is matched
    folded and the rows come back keyed by the canonical names, so a re-export
    that recases a column does not silently read every row as blank. It also lets
    the file carry a few lines of prose at the top, which ``model_specs.csv``
    uses to say the numbers in it are unverified.
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
            raise ModelSpecError(f"{path.name}: no header row ({','.join(header)})")


def _decimal(value: str | None, path: Path, line: int, column: str) -> Decimal:
    text = (value or "").strip()
    if not text:
        raise ModelSpecError(f"{path.name} line {line}: {column} is empty")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ModelSpecError(
            f"{path.name} line {line}: {column} is not a number ({value!r})"
        ) from None


def _int(value: str | None, path: Path, line: int, column: str,
         blank: int | None = ...) -> int | None:
    text = (value or "").strip()
    if not text:
        if blank is ...:
            raise ModelSpecError(f"{path.name} line {line}: {column} is empty")
        return blank
    try:
        return int(text)
    except ValueError:
        raise ModelSpecError(
            f"{path.name} line {line}: {column} is not a whole number ({value!r})"
        ) from None


def load_model_specs(path: Path = MODEL_SPECS_PATH) -> list[ModelSpec]:
    """Read ``model_specs.csv``. Raises :class:`ModelSpecError` on a bad edit."""
    specs: list[ModelSpec] = []
    for line, row in _rows(path, MODEL_SPEC_HEADER):
        make = row.get("make", "").strip()
        model = row.get("model", "").strip()
        if not make or not model:
            raise ModelSpecError(f"{path.name} line {line}: make and model are required")

        year_from = _int(row.get("year_from"), path, line, "year_from")
        year_to = _int(row.get("year_to"), path, line, "year_to")
        if year_to < year_from:
            raise ModelSpecError(
                f"{path.name} line {line}: year_to {year_to} is before "
                f"year_from {year_from}"
            )

        spec = ModelSpec(
            make=make,
            model=model,
            year_from=year_from,
            year_to=year_to,
            length_cm=_decimal(row.get("length_cm"), path, line, "length_cm"),
            width_cm=_decimal(row.get("width_cm"), path, line, "width_cm"),
            height_cm=_decimal(row.get("height_cm"), path, line, "height_cm"),
            co2_gkm=_int(row.get("co2_gkm"), path, line, "co2_gkm", blank=None),
            # Blank is allowed on all three and means three different things.
            # An empty co2_gkm blanks the whole landed cost, said out loud on
            # every card that matches the row; an empty euro_standard costs no
            # surcharge and is flagged; an empty fuel takes the dearer column.
            # None of them is a load error, because a hole in one row must not
            # cost the other three rows their prices — the same reason
            # `ModelSpecs` degrades a bad file to a reason string.
            euro_standard=row.get("euro_standard", "").strip() or None,
            fuel=row.get("fuel", "").strip() or None,
            body_model_code=row.get("body_model_code", "").strip() or None,
            line=line,
        )
        if spec.volume_m3 <= 0:
            raise ModelSpecError(
                f"{path.name} line {line}: dimensions give a volume of "
                f"{spec.volume_m3} m³"
            )
        specs.append(spec)

    _reject_overlaps(specs, path)
    return specs


def _reject_overlaps(specs: list[ModelSpec], path: Path) -> None:
    """Two rows that could both describe one car are an error, at load.

    Checked as *year-span overlap* rather than "two rows matched this car", for
    the reason ``bidding._reject_overlaps`` gives: at load time there is no car,
    and the stronger check catches a shadowed row on the day it is written rather
    than on the morning something finally falls in the gap.
    """
    groups: dict[tuple[str, str], list[ModelSpec]] = {}
    for spec in specs:
        groups.setdefault((_fold(spec.make), _fold(spec.model)), []).append(spec)

    for group in groups.values():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                if max(first.year_from, second.year_from) <= min(first.year_to, second.year_to):
                    raise ModelSpecError(
                        f"{path.name} lines {first.line} and {second.line}: "
                        f"{first.make} {first.model} year spans overlap "
                        f"({first.year_from}–{first.year_to} and "
                        f"{second.year_from}–{second.year_to})"
                    )


class ModelSpecs:
    """The spec table, loaded once; one pure lookup per car.

    A missing row is a ``None``, never a guess. Freight is 17% of the CNF price,
    so a car priced off a neighbouring model's dimensions would be wrong by more
    than any of the fees the calculator is careful about — and wrong invisibly.
    """

    def __init__(self, path: Path | None = None):
        self.reason: str | None = None
        try:
            self.specs = load_model_specs(path or MODEL_SPECS_PATH)
        except FileNotFoundError:
            self.specs, self.reason = [], "model specs not loaded"
        except (ModelSpecError, OSError, UnicodeDecodeError) as exc:
            self.specs, self.reason = [], f"model specs not loaded: {exc}"
        self.available = self.reason is None

    def for_car(self, make: str | None, model: str | None, year: int | None) -> ModelSpec | None:
        if not make or not model or year is None:
            return None
        key = (_fold(make), _fold(model))
        for spec in self.specs:
            if (_fold(spec.make), _fold(spec.model)) == key and spec.covers(year):
                return spec
        return None
