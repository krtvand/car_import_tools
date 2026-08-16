# Bid price in the report — open questions (grilling, round 1)

Feature: compute a **`bid_reduced`** for each lot in `banzai24/report.py` from a
table of maximum bids keyed by (make, model, mileage range, year, rental/private),
minus **extra costs that depend on the auction's location**, and show it on the report.

**Naming — fixed by Q18, used everywhere in this document:**
`bid_reduced = max_bid − extra_costs`. `max_bid` is the table value, `extra_costs`
is the auction house's area price, and **`bid_reduced` is the number you type into
the bidding platform**. The word "ceiling" is retired; earlier rounds that used it
mean `bid_reduced`.

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
maximum you are willing to **end up** at, and location costs are subtracted to yield what
you may actually bid at that house. Confirm the direction — and the currency (JPY, or EUR
converted at `--jpy-per-eur`?).

**Answer:** **`max_bid` = all-in maximum in JPY**; `bid_reduced = max_bid − extra_costs(auction)`.
JPY because it is the currency you bid in and every price on `AuctionLot` is JPY;

## Q3 — Which fields key the lookup, given each has two sources?

Mileage: API `mileage_km` (rounded) or sheet `sheet_mileage_km` (exact)? Year:
`registration_year` or the sheet's `first_registration_year`? Rental/private: sheet notes
only — with 54 of 62 lots having no extraction at all.

**Answer:** **Prefer the API** 


## Q4 — What does the report show when the lookup misses?

Four flavours of miss: rental/private unknown (the common case), no matching row for that
make/model/year/mileage, no cost entry for a new auction house, missing mileage or year.

**Answer:** **Show `null` for `bid_reduced`, say why. The house-cost (`extra_costs`) and
`max_bid` should still be displayed.** A guessed `bid_reduced` is worse than none on the
one number that spends money. 

## Q5 — How are the location costs keyed and shaped?


**Answer:** as csv file - banzai24/inputs/auction_area_prices_2026.csv

## Q6 — Is the computed `bid_reduced` stored in `auction.db`, or derived at report time?

`report.py`'s contract is read-only and free to regenerate.

**Answer:** **Derived, never stored.** A pure function in `bidding.py`, called from `collect()`, the
same shape as `CyprusPricer`. The table will change more often than the lots do; a stored
number goes stale silently, and a stale `bid_reduced` is exactly the failure that costs money.


## Q7 — Should `bid_reduced` drive a flag and the sort order?


**Answer:** no

## Q8 — Does this feed Phase 5 (the bidding runbook), or stop at the report?

`AUCTION_PLAN.md` currently says max bids reach the browser by you naming one per lot at
bid time.

➡️ **Stop at the report for now**, and record in `AUCTION_PLAN.md` that "Still open #2" is
half-answered: the report computes `bid_reduced`, Phase 5 still requires you to state the number
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

Q2 fixed `max_bid` as the **all-in maximum**. All-in from Japan to Cyprus is
auction fee + area/inland transport + export/shipping + duty & VAT. Subtracting
only ¥4,000–¥47,000 of area cost makes `bid_reduced` nearly equal to `max_bid`.

➡️ (a) Is there a **fixed base cost** to subtract alongside the per-house area
price — and does it live as a CLI flag, a constant, or a costs-CSV row? (b) Or is
the max-bid table already net of fixed costs, so `max_bid` means "hammer maximum
before area cost only"? Recommendation: **(a), a single `--base-cost-jpy` flag
defaulting to 0** — today's behaviour is exactly as described, and `bid_reduced`
becomes honest the moment you know the number.

**Answer:** yes. area price is the only thing subtracted, i.e. `extra_costs` is
the house's `AREA PRICE JPY` and nothing else.

## Q12 — Rental/private keys the lookup, but 54 of 62 lots don't know it

Under Q4 as written, those 54 all print a null `bid_reduced` — dark on 87% of the report.

➡️ **Blank rental/private column = "either".** Exact row first, blank row as
fallback, null only when no blank row exists for that car. Keeps Q4's
never-guess rule while letting you write one row per car for now.

**Answer:** keep using 'null'

## Q13 — `bid_prices.csv` column shape and band semantics

Proposed: `make,model,year_min,year_max,mileage_min,mileage_max,rental,max_bid_jpy`.

➡️ Bands **inclusive both ends**, blank = open-ended, mileage in km. Two rows
matching one lot is a **load-time error**, not first-match-wins — a silently
shadowed row is a wrong `bid_reduced` you never see. `make`/`model` normalised the same
way `MAZDA`/`CX-30` is matched today.

**Answer:** `make,model,year,mileage_min,mileage_max,rental,max_bid_jpy`.

## Q14 — Which mileage/year exactly, given "prefer the API" (Q3)?

**Answer:** **Fall back to the sheet when the API field is null**. Never the reverse — the API wins whenever both
exist. Refusing when the exact number is sitting in `SheetExtraction` is a null
you would only work around by hand.


## Q15 — Missing vs malformed input files

**Answer:** **Absent = `bazaraki.db` precedent**: no `bid_reduced` anywhere, a visible reason, no crash.
**Present-and-malformed = hard error** (bad band, duplicate row, unparseable
number) — that is your edit, and silently dropping it is worse than stopping.
The parser also **skips a leading title line** so a fresh Numbers export drops in
without hand-editing.



## Q16 — Where on the report does it appear?

**Answer:** A three-line money block per card, labelled with the Q18 names:
`max bid ¥X · area (U Tokyo) −¥Y · bid reduced ¥Z`, where `Z = X − Y`, with the
reason in place of `Z` when null (`no table row for 2017`). Not in the header
summary (Q7: no flag, no sort change).



## Q17 — What survives the removal of Phase 5?

**Answer:**  **Remove `BidRecord` and the report's bid flag** 

---

## Round 3 (blocked on the above)

- Input paths and flag names (`--bids` / `--area-costs`) — depends on Q11a, Q15.
- Whether the `_2026` in the filename is picked by year or is just a default path.
- Test seams: pure lookup against fixture tables vs a rendered-report assertion —
  depends on Q13's error semantics.
---

# Round 3

## Facts established (looked up, not to be answered)

- `bidrecord` holds **0 rows**. Removing it costs no data.
- `BidRecord` is referenced in 5 places in code (`models.py:129`, `db.py:195`
  `bids_by_numbers`, `report.py:34/74/258/432`), 5 in the template (the `--bid`
  CSS var, `.lot.bid`, `.badge.bid`, the bid block at `report.html.j2:287-293`
  and its `not yet bid` fallback), and 3 in `test_report.py`.
- The `report` command already takes `--jpy-per-eur`, used for start prices.

## Q18 — Q16 reversed the arithmetic Q2 fixed. Which is it?

Q2: `bid_ceiling = max_bid − extra_costs`. Q16: `bid ¥X · area −¥Y · **total
cost** ¥Z`. "Total cost" reads as `X + Y`, not `X − Y`.

- **(A) Ceiling** — `X` is the table's all-in max bid, `Z = X − Y` is what you may
  hammer at this house. Z < X. This is Q2.
- **(B) Total cost** — `X` is what you would bid, `Z = X + Y` is what that bid
  costs you landed. Z > X. This is what Q16's label says.

➡️ **(A)**, label fixed to `ceiling`. Under (B), `max_bid_jpy` would have to mean
"hammer price", which contradicts Q2's all-in definition — so if you want (B),
Q2's answer needs rewriting too. State which, and what `X` is labelled.

**Answer:** **(A)'s arithmetic, with (A)'s vocabulary replaced.** The subtraction
of Q2 stands; only the names change, and these names are now used throughout this
document:

- **`max_bid`** — the value in `bid_prices.csv`; the all-in maximum for that car (Q2).
- **`extra_costs`** — the auction house's `AREA PRICE JPY`; the only thing
  subtracted (Q10, Q11).
- **`bid_reduced`** — `max_bid − extra_costs`; **the price to enter on the bidding
  platform**.

`bid_reduced < max_bid` always, since `extra_costs > 0`. "Ceiling" and "total cost"
are both retired; wherever an earlier round said "ceiling", read `bid_reduced`.
Q16's block is relabelled accordingly.


## Q19 — `year` is singular now; edges and duplicates still open

**Answer:** **Exact year match** — one row per year per mileage band. No "does 2018 mean
2018+" ambiguity, and an unpriced year returns null rather than borrowing a
neighbouring year's number. **`mileage_min`/`mileage_max` inclusive both ends**,
blank = open-ended. **Two rows matching one lot = load-time error** (Q15).


## Q20 — What may the `rental` column hold?

Under Q12 a blank cell can never match: every lot either has a sheet note or gets
a null `bid_reduced` without consulting the table.

**Answer:** **Exactly `rental` or `private`; blank is a load-time error** — a row that can
never match is a typo, and Q15 says catch edits loudly. Accepted consequence:
with 7 private and 0 rental extractions today, at most 7 of 62 lots can show a
`bid_reduced`.


## Q21 — Alias CSV: path, columns, unmatched houses

Do we still fold-normalise (uppercase, strip punctuation/spaces), or is the alias
file the only mechanism? Normalising alone fixes `BAY AUC`→`BAYAUC`.

**Answer:** `banzai24/inputs/auction_aliases.csv`, columns `db_name,area_price_name`,
**plus** fold-normalisation — the file then carries only the six that genuinely
differ, not all seventeen. A house with neither alias nor fold-match gets a
**null `bid_reduced` and a reason, not an error**: new houses appear over time and a
report should not start failing because banzai24 added one.



## Q22 — Flags, defaults, and the `_2026` in the filename

**Answer:** `--bid-prices` and `--area-prices` on `report`, defaulting to
`banzai24/inputs/bid_prices.csv` and
`banzai24/inputs/auction_area_prices_2026.csv`. **The year is not computed** — a
clock-derived path silently loses every `bid_reduced` on 1 January.



## Q23 — How far does removing `BidRecord` go?

**Answer:** **Delete the code, delete the physical table.** Model, `db.bids_by_numbers`,
`LotView.bids`, the `BID` flag and its severity constant, the template block and
its CSS, and the three test references all go; write migration.



## Q24 — EUR, or JPY only?

**Answer:** **JPY only.** `bid_reduced` is a JPY decision and the row is three numbers wide
already;



## Q25 — Test seams

**Answer:** New `banzai24/tests/test_bidding.py` against tiny fixture CSVs: band edges
(min and max both hit), exact-year miss, alias hit, fold-only hit, unknown house,
duplicate row raising, absent file returning "not loaded", each null reason. Plus
**two** assertions in `test_report.py` — the money block's three numbers, and a
null `bid_reduced`'s reason. The lookup is pure, so nothing expensive enters the render
path.


