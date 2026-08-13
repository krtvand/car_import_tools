# Bid price in the report — open questions (grilling, round 1)

Feature: compute a **bid ceiling** for each lot in `banzai24/report.py` from a
table of maximum bids keyed by (make, model, mileage range, year, rental/private),
minus **extra costs that depend on the auction's location**, and show it on the report.

Answer inline under each ➡️ (overrides welcome). When the tree is empty, this
becomes a spec.

## Facts already established (looked up, not to be answered)

- `auction.db` holds 62 lots across **17 auction houses** — `U Tokyo` 10, `U Nagoya` 7,
  `U Kyushu` 6, `HAA Kobe` 5, `CAA Chubu` 5, `TAA Kyushu` 5, `MIRIVE Saitama` 5, … each
  with a stable `auction_id`. "Location" ≈ auction house, and the set grows over time.
- **Rental/private lives only on the auction sheet** (`SheetExtraction.rental_car_note` /
  `private_car_note`), and only 8 of 62 lots have an extraction at all: 7 private, 0 rental.
  Both-null is a real state ("company car, or the field was unreadable"), not a gap.
- **Two mileages**: `AuctionLot.mileage_km` (API, rounded to 1,000) vs
  `SheetExtraction.sheet_mileage_km` (exact — 15,415 vs 15,000). **Two years**:
  `registration_year` (API) vs `first_registration_year` (sheet). Either pair can straddle
  a band boundary.
- `report.py` is documented **read-only and free to regenerate** — no network, no model
  call, no DB writes.
- `AUCTION_PLAN.md` → "Still open #2" currently states that bid amounts deliberately have
  **no home in this pipeline**. This feature reverses half of that, so the decision needs
  recording there.

---

## Q1 — Where does the max-bid table live, and in what format?

A Python literal in a `config.py`-style module, a data file (CSV/YAML) editable outside the
code, or a DB table? It is a list you will re-tune often, and the key is composite
(make, model, mileage range, year, rental/private).

**Answer:** **A CSV file** (`banzai24/bid_prices.csv`, path overridable by a CLI flag), loaded by a
new `banzai24/bidding.py`. CSV because the list is already spreadsheet-shaped, it diffs
readably in git, and re-tuning it never means editing code. Not the DB: nothing else among
the report's inputs is operator-authored, and a table needs a migration plus an editor.


## Q2 — Is a "max bid" the hammer price or the all-in landed cost?

The feature as stated ("get extra costs from max bid price") reads as: the list holds the
ceiling you are willing to **end up** at, and location costs are subtracted to yield what
you may actually bid at that house. Confirm the direction — and the currency (JPY, or EUR
converted at `--jpy-per-eur`?).

**Answer:** **Max bid = all-in ceiling in JPY**; `bid_ceiling = max_bid − extra_costs(auction)`.
JPY because it is the currency you bid in and every price on `AuctionLot` is JPY;

## Q3 — Which fields key the lookup, given each has two sources?

Mileage: API `mileage_km` (rounded) or sheet `sheet_mileage_km` (exact)? Year:
`registration_year` or the sheet's `first_registration_year`? Rental/private: sheet notes
only — with 54 of 62 lots having no extraction at all.

**Answer:** **Prefer the API** 


## Q4 — What does the report show when the lookup misses?

Four flavours of miss: rental/private unknown (the common case), no matching row for that
make/model/year/mileage, no cost entry for a new auction house, missing mileage or year.

**Answer:** **Show `null`.  say why. the house-cost and max bid price should still be displayed.** A guessed ceiling
is worse than none on the one number that spends money. 

## Q5 — How are the location costs keyed and shaped?


**Answer:** as csv file - banzai24/inputs/auction_area_prices_2026.csv

## Q6 — Is the computed ceiling stored in `auction.db`, or derived at report time?

`report.py`'s contract is read-only and free to regenerate.

**Answer:** **Derived, never stored.** A pure function in `bidding.py`, called from `collect()`, the
same shape as `CyprusPricer`. The table will change more often than the lots do; a stored
number goes stale silently, and a stale ceiling is exactly the failure that costs money.


## Q7 — Should the ceiling drive a flag and the sort order?


**Answer:** no

## Q8 — Does this feed Phase 5 (the bidding runbook), or stop at the report?

`AUCTION_PLAN.md` currently says max bids reach the browser by you naming one per lot at
bid time.

➡️ **Stop at the report for now**, and record in `AUCTION_PLAN.md` that "Still open #2" is
half-answered: the report computes a ceiling, Phase 5 still requires you to state the number
out loud. Wiring a computed number straight into an irreversible bid is a separate decision
with a much larger blast radius.

**Answer:** **Stop at the report for now**, and remove Phase 5 (the bidding runbook) from `AUCTION_PLAN.md`. I will run grilling session specifically for this proces later

---

## Round 2 (blocked on the answers above)

- CSV column shape and band semantics — inclusive/exclusive edges, overlapping rows,
  open-ended top band, precedence when two rows match.
- CLI surface: flags for the two table paths, and behaviour when a file is absent
  (silent skip, like `bazaraki.db`, or an error).
- Test seams: pure lookup against fixture tables vs a rendered-report assertion.