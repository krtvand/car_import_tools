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
---

# Round 2

## Facts established (looked up, not to be answered)

- **House names diverge between `auction.db` and the cost CSV.** The DB says
  `U Tokyo`, `U Nagoya`, `U Kyushu`, `U Osaka`, `U Yokohama`, `Honda AA Tokyo`,
  `BAY AUC`; the CSV says `USS TOKYO`, `USS NAGOYA`, …, `HONDA TOKYO`, `BAYAUC`.
  Only 10 of 17 houses match on an uppercase fold; the 7 that miss cover
  **30 of 62 lots**. `auction_id` is stable and unambiguous where the name is not
  (39=U Tokyo, 45=U Nagoya, 50=U Kyushu, 46=U Osaka, 40=U Yokohama,
  106=Honda AA Tokyo, 123=BAY AUC).
- **`auction_area_prices_2026.csv` has two price columns** — `AREA PRICE $` and
  `AREA PRICE JPY` — and they are not one conversion (35→4000 is ¥114/$,
  365→47000 is ¥129/$). Two separately quoted prices.
- The file has a **title line above the header** (`auction_area_prices_2026`), so
  `csv.DictReader` misparses it as shipped. A Numbers re-export will keep it.
- Precedent for a missing input: `bazaraki.db` absent is not an error — the
  report renders and `cyprus_reason` says why.
- Removing Phase 5 orphans `BidRecord` ("written after a bid by Phase 5"), the
  report's `BID` flag (severity 40, reads that table), and "Still open #1"
  (bidding site URL, blocks Phase 5 only).

## Q9 — How does a lot's house name reach a CSV row?

`U Tokyo` (10 lots) fold-matches nothing; the file offers `USS TOKYO`, `JU TOKYO`,
`NPS TOKYO`, `CAA TOKYO`, `LUM TOKYO`, `NAA TOKYO`, `ZIP TOKYO`, `HONDA TOKYO`.
Whether banzai24's `U ` prefix means USS is yours to state.

➡️ Normalise both sides (uppercase, strip non-alphanumerics) — which fixes
`BAY AUC` on its own — **plus an explicit alias map keyed on `auction_id`** in
`bidding.py`, one commented entry each:
`{39: "USS TOKYO", 45: "USS NAGOYA", 50: "USS KYUSHU", 46: "USS OSAKA",
40: "USS YOKOHAMA", 106: "HONDA TOKYO"}`. Keyed on the id because the id is
stable and the name is display text. Confirm all six, especially `U ` = USS.

**Answer:** add new csv with aliases. example: "U Tokyo", "USS TOKYO" - it is the same auction.

## Q10 — Which cost column is authoritative?

➡️ **`AREA PRICE JPY`; ignore `AREA PRICE $`.** You bid in JPY (Q2), so one
number and no rate risk. The `$` column stays in the file for your own reading.

**Answer:** AREA PRICE JPY

## Q11 — Is the area price the only thing subtracted?

Q2 fixed max bid as the **all-in ceiling**. All-in from Japan to Cyprus is
auction fee + area/inland transport + export/shipping + duty & VAT. Subtracting
only ¥4,000–¥47,000 of area cost makes the "ceiling" nearly equal to the max bid.

➡️ (a) Is there a **fixed base cost** to subtract alongside the per-house area
price — and does it live as a CLI flag, a constant, or a costs-CSV row? (b) Or is
the max-bid table already net of fixed costs, so `max_bid` means "hammer ceiling
before area cost only"? Recommendation: **(a), a single `--base-cost-jpy` flag
defaulting to 0** — today's behaviour is exactly as described, and the ceiling
becomes honest the moment you know the number.

**Answer:** yes. area price is the only thing subtracted

## Q12 — Rental/private keys the lookup, but 54 of 62 lots don't know it

Under Q4 as written, those 54 all print a null ceiling — dark on 87% of the report.

➡️ **Blank rental/private column = "either".** Exact row first, blank row as
fallback, null only when no blank row exists for that car. Keeps Q4's
never-guess rule while letting you write one row per car for now.

**Answer:** keep using 'null'

## Q13 — `bid_prices.csv` column shape and band semantics

Proposed: `make,model,year_min,year_max,mileage_min,mileage_max,rental,max_bid_jpy`.

➡️ Bands **inclusive both ends**, blank = open-ended, mileage in km. Two rows
matching one lot is a **load-time error**, not first-match-wins — a silently
shadowed row is a wrong ceiling you never see. `make`/`model` normalised the same
way `MAZDA`/`CX-30` is matched today.

**Answer:** `make,model,year,mileage_min,mileage_max,rental,max_bid_jpy`.

## Q14 — Which mileage/year exactly, given "prefer the API" (Q3)?

**Answer:** **Fall back to the sheet when the API field is null**. Never the reverse — the API wins whenever both
exist. Refusing when the exact number is sitting in `SheetExtraction` is a null
you would only work around by hand.


## Q15 — Missing vs malformed input files

**Answer:** **Absent = `bazaraki.db` precedent**: no ceilings, a visible reason, no crash.
**Present-and-malformed = hard error** (bad band, duplicate row, unparseable
number) — that is your edit, and silently dropping it is worse than stopping.
The parser also **skips a leading title line** so a fresh Numbers export drops in
without hand-editing.



## Q16 — Where on the report does it appear?

**Answer:** A three-line money block per card:
`bid ¥X · area (U Tokyo) −¥Y · total cost ¥Z`, with the reason in place of `Z`
when null (`no table row for 2017`). Not in the header summary (Q7: no flag, no sort change).



## Q17 — What survives the removal of Phase 5?

**Answer:**  **Remove `BidRecord` and the report's bid flag** 

---

## Round 3 (blocked on the above)

- Input paths and flag names (`--bids` / `--area-costs`) — depends on Q11a, Q15.
- Whether the `_2026` in the filename is picked by year or is just a default path.
- Test seams: pure lookup against fixture tables vs a rendered-report assertion —
  depends on Q13's error semantics.
