"""``python -m price_calculator`` — every row of ``bid_prices.csv``, priced and compared.

Four rows you can check by hand, which is the point: this exists so you find out
whether the landed cost is right *before* the same arithmetic starts printing
itself on sixty report cards. It answers "are my max bids sane?", not "should I
bid on this car" — the report answers that one, off the same engine.

Each row is priced at the **top** of its mileage band. That is where the cars you
actually import sit, and pricing at the band's midpoint would flatter every
number on the page. Where a fitted curve has the sign of depreciation backwards
the top of the band is no longer the conservative end, and the row says so.

Prices come from the cost book (``inputs/costs.toml``) and are printed in the
header, so a table you print today says which price list it was priced against.
A cost book that will not load stops the run before a single row is built.

Rates are fetched live at invocation and printed in the header, because a
standalone run has no run directory to stamp them into. ``--eur-jpy`` and
``--usd-jpy`` skip the network entirely, which is how the tests price the
reference car and how you get a table on a train.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from banzai24.bidding import BID_PRICES_PATH, BidTableError, load_bid_prices

from .calculator import CostBook, Margin, Rates
from .sources import (
    COSTS_PATH,
    MODEL_SPECS_PATH,
    CostBookError,
    CyprusMarket,
    ModelSpecs,
    RatesUnavailable,
    fetch_rates,
    load_cost_book,
    margin_for,
)


def _rates(args) -> Rates:
    """Injected rates if both were given, otherwise today's from the ECB."""
    if args.eur_jpy is not None and args.usd_jpy is not None:
        return Rates(
            usd_jpy=Decimal(str(args.usd_jpy)),
            eur_jpy_market=Decimal(str(args.eur_jpy)),
            fetched_at=datetime.now(timezone.utc),
            source="--eur-jpy/--usd-jpy",
        )
    if (args.eur_jpy is None) != (args.usd_jpy is None):
        raise SystemExit("--eur-jpy and --usd-jpy must be given together")
    return fetch_rates()


def _band(row) -> tuple[int, str]:
    """``(mileage to price at, label)`` — the top of the band, where possible.

    An open-ended band has no top, so it is priced at its floor and labelled
    ``60,001+ km``. That is the *optimistic* end of an unbounded range, and the
    honest way to show it is to leave the label unbounded rather than invent a
    ceiling that would look like a measurement.
    """
    if row.mileage_max is not None:
        return row.mileage_max, f"{row.mileage_min:,}–{row.mileage_max:,} km"
    return row.mileage_min, f"{row.mileage_min:,}+ km"


def _cell(margin: Margin | str, attr: str) -> str:
    if isinstance(margin, str):
        return "—"
    value = getattr(margin, attr)
    if value is None:
        return "—"
    if attr == "margin_pct":
        return f"{value:+.1f}%"
    return f"€{value:,.0f}"


def build_rows(bid_prices_path: Path, model_specs_path: Path,
               rates: Rates, costs: CostBook):
    """``[(label, max_bid, margin_or_reason), …]`` in the order the CSV lists them."""
    rows = load_bid_prices(bid_prices_path)
    specs = ModelSpecs(model_specs_path)
    market = CyprusMarket()

    out = []
    for row in rows:
        mileage, band = _band(row)
        label = f"{row.make} {row.model} {row.year} · {band} · {row.rental}"
        result = margin_for(
            make=row.make, model=row.model, year=row.year, mileage_km=mileage,
            auction_price_jpy=row.max_bid_jpy, rates=rates, costs=costs,
            specs=specs, market=market,
        )
        out.append((label, row.max_bid_jpy, result))
    return out, specs, market


def render(rows, rates: Rates, costs: CostBook, specs: ModelSpecs,
           market: CyprusMarket, model_specs_path: Path) -> str:
    lines = [
        "Imported car price calculator — landed cost against the Cyprus market",
        f"rates   {rates.describe()}",
        f"prices  {costs.describe()}",
        f"specs   {model_specs_path}",
    ]
    if specs.reason:
        lines.append(f"        !! {specs.reason}")
    if market.reason:
        lines.append(f"        !! {market.reason}")
    lines.append("")

    width = max((len(label) for label, _, _ in rows), default=20)
    header = (f"{'CAR'.ljust(width)}  {'MAX BID':>12}  {'LANDED':>9}  "
              f"{'CYPRUS':>9}  {'MARGIN':>9}  {'':>7}")
    lines += [header, "-" * len(header)]

    for label, max_bid, result in rows:
        landed = ("—" if isinstance(result, str)
                  else f"€{result.landed.total_eur:,.0f}")
        lines.append(
            f"{label.ljust(width)}  {'¥' + format(max_bid, ',d'):>12}  {landed:>9}  "
            f"{_cell(result, 'cyprus_eur'):>9}  {_cell(result, 'gap_eur'):>9}  "
            f"{_cell(result, 'margin_pct'):>7}"
        )
        if isinstance(result, str):
            lines.append(f"{' ' * width}  → {result}")
            continue

        # The notes carry the assumptions the columns above cannot. The resale
        # factor is here rather than buried because after the survivorship fix
        # it is the single largest one in the chain: ×0.920 means "no sale data,
        # falling back to the default haircut", not "measured at 8%".
        notes = []
        if result.reason:
            notes.append(result.reason)
        if result.adjustment_factor is not None:
            notes.append(f"resale ×{result.adjustment_factor:.3f} · "
                         f"Cyprus fit {result.cyprus_confidence}")
        if result.warning:
            notes.append(result.warning)
        if result.landed.above_fee_table:
            notes.append("auction price is off the end of the exporter fee table")
        for note in notes:
            lines.append(f"{' ' * width}  → {note}")

    lines += [
        "",
        "landed = hammer price to Cyprus plates, your margin excluded.",
        "Cyprus = resale estimate at the top of the band; margin is net of resale costs.",
        "Model spec dimensions are seeded and unverified — check them before trusting a margin.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m price_calculator",
        description="Price every row of bid_prices.csv against the Cyprus market.")
    parser.add_argument("--bid-prices", type=Path, default=BID_PRICES_PATH)
    parser.add_argument("--model-specs", type=Path, default=MODEL_SPECS_PATH)
    parser.add_argument("--eur-jpy", type=float, default=None,
                        help="skip the rate fetch (market rate; the −2 haircut still applies)")
    parser.add_argument("--usd-jpy", type=float, default=None,
                        help="skip the rate fetch")
    parser.add_argument("--costs", type=Path, default=COSTS_PATH,
                        help="the cost book to price with (default inputs/costs.toml)")
    parser.add_argument("--resale-costs", type=Decimal, default=None,
                        help="override the book's resale costs, EUR off the margin")
    args = parser.parse_args(argv)

    try:
        costs = load_cost_book(args.costs)
    except CostBookError as exc:
        print(f"could not read the cost book: {exc}", file=sys.stderr)
        return 2
    if args.resale_costs is not None:
        costs = replace(costs, resale_costs_eur=args.resale_costs)

    try:
        rates = _rates(args)
    except RatesUnavailable as exc:
        print(f"could not fetch exchange rates: {exc}\n"
              f"pass --eur-jpy and --usd-jpy to price without the network.",
              file=sys.stderr)
        return 2

    try:
        rows, specs, market = build_rows(
            args.bid_prices, args.model_specs, rates, costs)
    except (BidTableError, FileNotFoundError, OSError) as exc:
        print(f"could not read {args.bid_prices}: {exc}", file=sys.stderr)
        return 2

    print(render(rows, rates, costs, specs, market, args.model_specs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
