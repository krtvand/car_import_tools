"""Where the calculator's inputs come from: a CSV, a TOML, a rate API, two databases.

:mod:`price_calculator.calculator` is pure and knows none of this. Everything
that reads a file, opens a socket or looks at a clock lives here — the same
split :mod:`bazaraki.analysis` already documents ("deliberately kept free of
database and network dependencies … thin DB-backed wrappers live at the
bottom"), because the arithmetic is the part worth testing against the sheet and
it cannot be tested against the sheet if it needs a network to run.

The dependency runs one way. ``banzai24.report`` imports this; this imports
``banzai24.bidding`` and ``bazaraki.analysis``. Nothing here imports back.

``bid_prices.csv`` is **not** re-parsed here — :func:`banzai24.bidding.load_bid_prices`
already reads it, validates the bands and rejects overlaps, and a second parser
for the same file is a second place for the two to disagree about what a blank
``mileage_max`` means.
"""
from __future__ import annotations

import csv
import json
import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .calculator import (
    CostBook,
    Margin,
    ModelSpec,
    Rates,
    ServiceFeeTier,
    landed_cost,
)

INPUTS_DIR = Path(__file__).parent / "inputs"
MODEL_SPECS_PATH = INPUTS_DIR / "model_specs.csv"
COSTS_PATH = INPUTS_DIR / "costs.toml"

MODEL_SPEC_HEADER = ("make", "model", "year_from", "year_to", "length_cm",
                     "width_cm", "height_cm", "co2_gkm", "body_model_code")


class ModelSpecError(ValueError):
    """A spec table is on disk but cannot be trusted — a bad year span, a duplicate.

    Raised by the loader so a test can assert on the edit that caused it.
    Callers catch it and degrade to a reason string, so a mis-edited CSV costs
    you the landed cost and not the page.
    """


def _fold(value: str | None) -> str:
    """The same fold ``banzai24.bidding`` and ``bazaraki.analysis`` use.

    Shared by copy rather than by import because both of theirs are private, and
    a public re-export would make this module the owner of a convention it does
    not own. It is one regex, and a test asserts the three agree.
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
    than any of the fees this module is careful about — and wrong invisibly.
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


# --- the cost book -----------------------------------------------------------

COSTS_FILENAME = "costs.json"


class CostBookError(ValueError):
    """``costs.toml`` is unreadable, incomplete, or holds something that is not a price.

    Unlike :class:`ModelSpecError` this is **not** degraded into a ``reason``
    string by its callers, and the difference is blast radius. A missing model
    spec costs you one card; a fat-fingered comma in the cost book makes every
    number on every card wrong, and forty rows silently reading "no landed cost"
    is a worse morning than one line on stderr. See ADR 0003.
    """


def _at(payload: dict, dotted: str, path: Path):
    """The value at ``exporter.fixed_fee_jpy``, or a ``CostBookError`` naming it."""
    node = payload
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            raise CostBookError(f"{path.name}: missing {dotted}")
        node = node[key]
    return node


def _price(payload: dict, dotted: str, path: Path) -> Decimal:
    """One number from the file, as a :class:`~decimal.Decimal`.

    A TOML float is rejected rather than converted. ``vat_rate = 0.19`` parses to
    a binary float that is not 0.19, and a fraction of a cent per car is exactly
    the kind of drift that makes a port disagree with the spreadsheet for no
    findable reason — so rates are written quoted, and this says so when they
    are not.
    """
    value = _at(payload, dotted, path)
    if isinstance(value, float):
        raise CostBookError(
            f"{path.name}: {dotted} is a decimal number ({value}) — quote it "
            f'as a string ("{value}") so it is read exactly')
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CostBookError(f"{path.name}: {dotted} is not a number ({value!r})")
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        raise CostBookError(f"{path.name}: {dotted} is not a number ({value!r})") from None


def load_cost_book(path: Path = COSTS_PATH) -> CostBook:
    """Read ``costs.toml``. Raises :class:`CostBookError` on anything at all.

    Every field is required. There is no default, no partial book and no
    last-known-good copy in code, because that copy is the one that would go
    stale unnoticed — which is the failure ADR 0003 exists to prevent.
    """
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CostBookError(f"{path}: no cost book (expected {path.name})") from None
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise CostBookError(f"{path.name}: {exc}") from exc

    rows = _at(payload, "exporter.service_fee", path)
    if not isinstance(rows, list) or not rows:
        raise CostBookError(f"{path.name}: exporter.service_fee has no tiers")
    tiers = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise CostBookError(f"{path.name}: exporter.service_fee #{index} is not a table")
        tiers.append(ServiceFeeTier(
            up_to_jpy=_price({"t": row}, "t.up_to_jpy", path),
            fee_jpy=_price({"t": row}, "t.fee_jpy", path),
        ))

    updated = payload.get("updated")
    if updated is not None and not isinstance(updated, date):
        raise CostBookError(f"{path.name}: updated is not a date ({updated!r})")

    book = CostBook(
        service_fee_tiers=tuple(tiers),
        exporter_fixed_fee_jpy=_price(payload, "exporter.fixed_fee_jpy", path),
        certificate_of_origin_jpy=_price(payload, "exporter.certificate_of_origin_jpy", path),
        roro_per_m3_usd=_price(payload, "freight.roro_per_m3_usd", path),
        freight_insurance_usd=_price(payload, "freight.insurance_usd", path),
        vat_rate=_price(payload, "taxes.vat_rate", path),
        duty_rate=_price(payload, "taxes.duty_rate", path),
        bank_fx_rate=_price(payload, "bank.fx_rate", path),
        international_transfer_eur=_price(payload, "bank.international_transfer_eur", path),
        eur_jpy_spread=_price(payload, "bank.eur_jpy_spread", path),
        sva_test_eur=_price(payload, "cyprus.sva_test_eur", path),
        mot_eur=_price(payload, "cyprus.mot_eur", path),
        registration_eur=_price(payload, "cyprus.registration_eur", path),
        customs_clearance_eur=_price(payload, "cyprus.customs_clearance_eur", path),
        number_plates_eur=_price(payload, "cyprus.number_plates_eur", path),
        car_service_eur=_price(payload, "cyprus.car_service_eur", path),
        insurance_eur=_price(payload, "cyprus.insurance_eur", path),
        road_tax_eur=_price(payload, "cyprus.road_tax_eur", path),
        resale_costs_eur=_price(payload, "resale.costs_eur", path),
        updated=updated,
        source=str(payload.get("source", "")),
    )

    problems = book.problems()
    if problems:
        raise CostBookError(f"{path.name}: " + "; ".join(problems))
    return book


def write_costs(run_dir: Path, costs: CostBook) -> Path:
    """Stamp the prices into a run, for the reason :func:`write_rates` gives.

    The rates were never the only thing that moves. Exporter service fees went up
    by ¥3,000–¥23,000 a band in August; a report that re-read today's book would
    reprice every car it had already priced, and August's page would stop
    matching August's decision.
    """
    path = run_dir / COSTS_FILENAME
    payload = {
        "service_fee_tiers": [{"up_to_jpy": str(t.up_to_jpy), "fee_jpy": str(t.fee_jpy)}
                              for t in costs.service_fee_tiers],
        "updated": costs.updated.isoformat() if costs.updated else None,
        "source": costs.source,
    }
    for field in ("exporter_fixed_fee_jpy", "certificate_of_origin_jpy",
                  "roro_per_m3_usd", "freight_insurance_usd", "vat_rate",
                  "duty_rate", "bank_fx_rate", "international_transfer_eur",
                  "eur_jpy_spread", "sva_test_eur", "mot_eur", "registration_eur",
                  "customs_clearance_eur", "number_plates_eur", "car_service_eur",
                  "insurance_eur", "road_tax_eur", "resale_costs_eur"):
        payload[field] = str(getattr(costs, field))
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_costs(run_dir: Path) -> CostBook | None:
    """The prices this run was quoted at, or ``None`` for a run predating them.

    ``None`` behaves exactly as a missing ``rates.json`` does: the cards carry no
    landed cost. Falling back to today's book would be the report claiming a car
    cost in August what it would cost now, which is the one thing stamping the
    book was for.
    """
    path = run_dir / COSTS_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated = payload.get("updated")
        return CostBook(
            service_fee_tiers=tuple(
                ServiceFeeTier(up_to_jpy=Decimal(row["up_to_jpy"]),
                               fee_jpy=Decimal(row["fee_jpy"]))
                for row in payload["service_fee_tiers"]),
            updated=date.fromisoformat(updated) if updated else None,
            source=payload.get("source", ""),
            **{field: Decimal(payload[field]) for field in (
                "exporter_fixed_fee_jpy", "certificate_of_origin_jpy",
                "roro_per_m3_usd", "freight_insurance_usd", "vat_rate",
                "duty_rate", "bank_fx_rate", "international_transfer_eur",
                "eur_jpy_spread", "sva_test_eur", "mot_eur", "registration_eur",
                "customs_clearance_eur", "number_plates_eur", "car_service_eur",
                "insurance_eur", "road_tax_eur", "resale_costs_eur")},
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError,
            InvalidOperation, OSError):
        return None


# --- exchange rates ----------------------------------------------------------

# The ECB's daily reference rates, free and keyless. Published around 16:00 CET
# on business days, so a Sunday fetch returns Friday's rates under Friday's
# ``date`` — which is correct rather than stale, and is why that date is carried
# on the quote instead of the fetch time alone.
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
RATES_FILENAME = "rates.json"


class RatesUnavailable(RuntimeError):
    """The rate API could not be reached, or answered with something unusable."""


def fetch_rates(url: str = FRANKFURTER_URL, timeout: float = 10.0) -> Rates:
    """Today's EUR/JPY and USD/JPY, from one request.

    Asked base-first (``base=JPY``) so both rates come from a single response and
    a single moment. The API quotes JPY per unit of *base*, so a JPY base yields
    EUR-per-yen and USD-per-yen, and the rates the calculator wants are their
    reciprocals.
    """
    import httpx

    try:
        response = httpx.get(url, params={"base": "JPY", "symbols": "EUR,USD"},
                             timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        quoted = payload["rates"]
        eur_per_jpy = Decimal(str(quoted["EUR"]))
        usd_per_jpy = Decimal(str(quoted["USD"]))
        if eur_per_jpy <= 0 or usd_per_jpy <= 0:
            raise ValueError(f"non-positive rate in {quoted}")
        as_of = datetime.fromisoformat(payload["date"]).replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise RatesUnavailable(f"{type(exc).__name__}: {exc}") from exc

    return Rates(
        usd_jpy=Decimal(1) / usd_per_jpy,
        eur_jpy_market=Decimal(1) / eur_per_jpy,
        fetched_at=as_of,
        source="frankfurter.dev (ECB)",
    )


def write_rates(run_dir: Path, rates: Rates) -> Path:
    """Stamp the rates into a run, so re-rendering it never moves the money.

    This is the whole reason ``report`` can keep its promise of costing nothing
    and touching no network. A landed cost re-computed at today's rate would mean
    the same car, on the same page, quietly disagreeing with the decision you
    made about it in August.
    """
    path = run_dir / RATES_FILENAME
    path.write_text(
        json.dumps({
            "usd_jpy": str(rates.usd_jpy),
            "eur_jpy_market": str(rates.eur_jpy_market),
            "fetched_at": rates.fetched_at.isoformat(),
            "source": rates.source,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def read_rates(run_dir: Path) -> Rates | None:
    """The rates this run was fetched at, or ``None`` for a run predating them.

    Runs fetched before rates were stamped simply have no landed cost on their
    cards. Inventing one from today's rate would be the report claiming to know
    what a car would have cost on a day it does not have the rate for.
    """
    path = run_dir / RATES_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Rates(
            usd_jpy=Decimal(payload["usd_jpy"]),
            eur_jpy_market=Decimal(payload["eur_jpy_market"]),
            fetched_at=datetime.fromisoformat(payload["fetched_at"]),
            source=payload.get("source", "unknown"),
        )
    except (FileNotFoundError, KeyError, ValueError, InvalidOperation, OSError):
        return None


# --- the Cyprus market -------------------------------------------------------


@dataclass(frozen=True)
class CyprusEstimate:
    """What the same car realistically sells for here, and how much to trust it."""

    sale_price: Decimal | None
    asking_estimate: Decimal | None
    adjustment_factor: Decimal
    n: int
    confidence: str
    reason: str | None = None
    warning: str | None = None


class CyprusMarket:
    """``bazaraki.db`` behind one call, with the fit's own sanity checked.

    Listings load once and per-model subsets are cached: a run is usually one
    model, so this is one query no matter how many lots it holds — the same
    shape as :class:`banzai24.report.CyprusPricer`, which this does not replace.
    That one answers "what are these asking?"; this one answers "what would it
    sell for?", and the second is a curve with a haircut on top.
    """

    def __init__(self, records=None):
        self._analysis = None
        self._records = records
        self._scoped: dict[tuple[str, str], list] = {}
        self._histories: dict[tuple[str, str], list] = {}
        self.reason: str | None = None

        try:
            from bazaraki import analysis
            self._analysis = analysis
            if self._records is None:
                from bazaraki import db as bazaraki_db
                self._records = analysis.exclude_availability(
                    analysis.to_records(bazaraki_db.all_listings()),
                    analysis.IN_TRANSIT,
                )
            self.available = True
        except Exception as exc:   # missing db, missing package, unreadable file
            self.available = False
            self.reason = f"Cyprus market not loaded: {type(exc).__name__}: {exc}"

    def _scope(self, make: str, model: str):
        key = (_fold(make), _fold(model))
        if key not in self._scoped:
            analysis = self._analysis
            self._scoped[key] = analysis.clean(
                analysis.filter_model(self._records, make, model))
            self._histories[key] = self._price_histories(self._scoped[key])
        return self._scoped[key], self._histories[key]

    def _price_histories(self, scoped) -> list:
        """Price trajectories for the scoped adverts — the price-cut signal's input.

        Silently empty when the database is not the real one (tests pass records
        in directly), which turns the price-cut signal off rather than faking it.
        """
        try:
            from bazaraki import db as bazaraki_db
            return [[o.price for o in bazaraki_db.price_history(r.ad_id)] for r in scoped]
        except Exception:
            return []

    def estimate(self, make: str | None, model: str | None,
                 year: int | None, mileage_km: int | None) -> CyprusEstimate:
        """The resale estimate for one car at one point, or why there isn't one.

        Priced at the exact ``(year, mileage_km)`` asked for. The standalone table
        asks at the **top** of each bid band, because that is where the cars you
        actually import sit; the report asks at the lot's own mileage.
        """
        empty = lambda why: CyprusEstimate(None, None, Decimal(1), 0, "none", reason=why)

        if not self.available:
            return empty(self.reason)
        if not make or not model:
            return empty("no make/model to compare")
        if year is None or mileage_km is None:
            return empty("no comparable query (needs year + mileage)")

        scoped, histories = self._scope(make, model)
        if not scoped:
            return empty(f"no Cyprus listings for {make} {model}")

        result = self._analysis.estimate_sale_price(
            self._records, make, model,
            year_range=(year, year),
            mileage_range=(mileage_km, mileage_km),
            histories=histories,
        )
        if result.sale_price is None:
            return empty(f"no Cyprus fit for {make} {model} (n={result.n})")

        return CyprusEstimate(
            sale_price=Decimal(str(result.sale_price)),
            asking_estimate=Decimal(str(result.asking_estimate)),
            adjustment_factor=Decimal(str(result.adjustment_factor)),
            n=result.n,
            confidence=result.confidence,
            warning=self._slope_warning(scoped, make, model),
        )

    def _slope_warning(self, scoped, make: str, model: str) -> str | None:
        """Flag a fitted curve that thinks mileage *adds* value.

        The RAV4's fit does exactly this today — ``+0.0032`` per 10,000 km, so it
        prices a 50,000 km car above a 25,000 km one. Since the table asks at the
        top of each band, that inversion turns the most conservative query into
        the most optimistic answer, silently. A curve that has the sign of
        depreciation backwards has not understood the car, and the number it
        produces is not one to bid against.
        """
        curve = self._analysis.fit_price_curve(scoped)
        if curve is None:
            return None
        per_10k = float(curve.beta[2])
        if per_10k >= 0:
            return (f"curve prices mileage upward ({per_10k:+.4f}/10k km) — "
                    f"the {make} {model} fit is unreliable")
        return None


# --- one car, priced and compared --------------------------------------------


def margin_for(
    make: str | None,
    model: str | None,
    year: int | None,
    mileage_km: int | None,
    auction_price_jpy: int | Decimal,
    rates: Rates,
    costs: CostBook,
    specs: ModelSpecs,
    market: CyprusMarket,
) -> Margin | str:
    """One car's landed cost against the Cyprus market, or a reason there is none.

    The cost book is a required argument and never loaded here: a book that
    cannot be read has already stopped the run (:class:`CostBookError`), so by
    the time one car is priced there is always exactly one set of prices in play
    and it is the one recorded on the answer.

    Returns a ``str`` only when the *landed cost itself* cannot be computed — a
    missing model spec, an unusable auction price. A missing **Cyprus** estimate
    still returns a :class:`Margin`, carrying the landed cost and the reason the
    comparison is blank: knowing a car lands at €17,946 is useful on a row that
    cannot say what it sells for, and the two halves fail independently.
    """
    spec = specs.for_car(make, model, year)
    if spec is None:
        if specs.reason:
            return specs.reason
        return f"no model spec for {make or '?'} {model or '?'} {year or '?'}"

    try:
        landed = landed_cost(auction_price_jpy, spec, rates, costs)
    except ValueError as exc:
        return str(exc)

    cyprus = market.estimate(make, model, year, mileage_km)
    return Margin(
        landed=landed,
        cyprus_eur=cyprus.sale_price,
        resale_costs_eur=costs.resale_costs_eur,
        cyprus_confidence=cyprus.confidence if cyprus.sale_price is not None else None,
        adjustment_factor=cyprus.adjustment_factor if cyprus.sale_price is not None else None,
        reason=cyprus.reason,
        warning=cyprus.warning,
    )
