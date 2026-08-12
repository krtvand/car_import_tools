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

➡️ **A CSV file** (`banzai24/bid_prices.csv`, path overridable by a CLI flag), loaded by a
new `banzai24/bidding.py`. CSV because the list is already spreadsheet-shaped, it diffs
readably in git, and re-tuning it never means editing code. Not the DB: nothing else among
the report's inputs is operator-authored, and a table needs a migration plus an editor.

**Answer:**

## Q2 — Is a "max bid" the hammer price or the all-in landed cost?

The feature as stated ("get extra costs from max bid price") reads as: the list holds the
ceiling you are willing to **end up** at, and location costs are subtracted to yield what
you may actually bid at that house. Confirm the direction — and the currency (JPY, or EUR
converted at `--jpy-per-eur`?).

➡️ **Max bid = all-in ceiling in JPY**; `bid_ceiling = max_bid − extra_costs(auction)`.
JPY because it is the currency you bid in and every price on `AuctionLot` is JPY; a EUR
list would make the ceiling drift with an FX rate the report deliberately refuses to
default.

**Answer:**

## Q3 — Which fields key the lookup, given each has two sources?

Mileage: API `mileage_km` (rounded) or sheet `sheet_mileage_km` (exact)? Year:
`registration_year` or the sheet's `first_registration_year`? Rental/private: sheet notes
only — with 54 of 62 lots having no extraction at all.

➡️ **Prefer the sheet, fall back to the API, and print which was used.** Sheet-exact
mileage is the truth, and 15,415 vs 15,000 can cross a band edge. Rental/private has no API
source at all, so most lots will be undetermined — which is Q4.

**Answer:**

## Q4 — What does the report show when the lookup misses?

Four flavours of miss: rental/private unknown (the common case), no matching row for that
make/model/year/mileage, no cost entry for a new auction house, missing mileage or year.

➡️ **Show nothing numeric, say why, and flag only the house-cost miss.** A guessed ceiling
is worse than none on the one number that spends money. Concretely: rental/private unknown
→ show **both branches** (`rental ¥1.42M · private ¥1.61M`) rather than picking one;
unmatched row → `no max-bid rule for 2019 CX-30 @ 60k km`; **unknown auction house → a
flag**, because that one is a real gap in your cost table.

Alternative if one number is wanted: take the lower (rental) branch and label it "assumed".

**Answer:**

## Q5 — How are the location costs keyed and shaped?

Per auction house (17 and counting), per house group (`U` / `TAA` / `CAA` / `JU` …), or per
region? A single flat JPY figure, or itemised components (inland transport, auction fee,
inspection) that sum?

➡️ **Per `auction_id`, flat JPY, with a named default for unlisted houses.** `auction_id`
is stable where a printed name may not be; a flat figure is what can be stated today, and
itemisation can arrive later as extra columns without changing the key. Group prefixes look
tempting, but `U Tokyo` and `U Kyushu` are opposite ends of the country.

**Answer:**

## Q6 — Is the computed ceiling stored in `auction.db`, or derived at report time?

`report.py`'s contract is read-only and free to regenerate.

➡️ **Derived, never stored.** A pure function in `bidding.py`, called from `collect()`, the
same shape as `CyprusPricer`. The table will change more often than the lots do; a stored
number goes stale silently, and a stale ceiling is exactly the failure that costs money.

**Answer:**

## Q7 — Should the ceiling drive a flag and the sort order?

The four existing flags are a strict severity ladder that doubles as the sort key
(mismatch 50, bid placed 40, low confidence 30, not read 10). Candidate new flag: **start
price already above your ceiling** — i.e. do not bother.

➡️ **Yes, but low severity (~20), between low-confidence and not-read.** It is a fact about
your *rules*, not about the car, and it risks firing on most of the page — the same
reasoning that kept a blank 車検 off the ladder. Plus the unknown-house flag from Q4 at the
same level.

**Answer:**

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