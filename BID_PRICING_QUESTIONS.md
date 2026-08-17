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

---

## Round 4 (blocked on the above)

- The one unconfirmed assumption in the document (`U ` = USS), and what else keys
  a max bid — depends on Q9/Q13/Q21.
- Failure semantics not yet pinned: negative `bid_reduced`, the closed set of null
  reasons, what "duplicate row" means at load time — depends on Q4/Q15/Q19/Q28.
- Mechanics: shipping the two new CSVs, dropping the physical `bidrecord` table,
  the reach of the plan-document edit, and `bidding.py`'s signature — depends on
  Q22/Q23/Q6.

---

# Round 4

## Facts established (looked up, not to be answered)

**Two facts asserted in earlier rounds are wrong:**

- **`mileage_km` is not always rounded to 1,000.** `auction.db` holds a lot at
  **7,497 km**. Round 1's "API, rounded to 1,000" is not a property of the field;
  the API value is simply what banzai24 reports, sometimes exact. Q3/Q14's rule
  ("prefer the API") stands, but its stated rationale does not.
- **The fold-miss list is smaller than Round 2 says.** Under Q21's fold
  (uppercase, strip non-alphanumerics) `BAY AUC`→`BAYAUC` matches. Exactly **six
  houses miss, covering 27 of 62 lots**: `U Tokyo` (10), `U Nagoya` (7),
  `U Kyushu` (6), `U Osaka` (2), `U Yokohama` (1), `Honda AA Tokyo` (1). Round 2's
  "7 houses / 30 lots" counted `BAY AUC` as a miss, which an uppercase-only fold
  would make true.

New:

- **`auction.db` holds one year and two cars**: every one of the 62 lots is
  `registration_year` **2023**, and they are MAZDA CX-30 (45) and TOYOTA RAV4 (17).
  Trim (`modification`) and auction grade (`grade_origin`) vary within each.
- **No lot is missing a year or a mileage.** Q14's sheet fallback fires on nothing
  in today's database.
- **`bid_prices.csv` exists**, authored by hand at `banzai24/inputs/bid_prices.csv`
  and staged in git. Header is exactly Q13's
  (`make,model,year,mileage_min,mileage_max,rental,max_bid_jpy`), plus two rows:
  `MAZDA,CX-30,2023,0,50000,private,1855000` and
  `MAZDA,CX-30,2023,50000,60000,private,1705000`.
- `auction_area_prices_2026.csv` is **123 rows**, names unique after folding, both
  price columns plain integers with no separators, one title line above the header.
- **There is no migration framework.** `db.init_db()` is `create_all` plus a
  column-healing pass that only adds nullable columns. The repo's precedent for a
  schema change is a standalone script (`bazaraki/migrate_make_model.py`).
- **`record_bid()` exists only in `AUCTION_PLAN.md`**, never in code. `BidRecord` is
  also documented at `AUCTION_PLAN.md:419` (storage model) and `:609` (report flags),
  and Phase 5 has a row in the Build-order table at `:717`.

## Q26 — Confirm `U ` = USS, on the record

This is the single unconfirmed assumption in the document and it decides 26 of
62 lots. Rounds 2 and 3 both proposed the mapping and neither answer states it.
`U Tokyo` fold-matches nothing; the file offers `USS TOKYO`, `JU TOKYO`,
`NPS TOKYO`, `CAA TOKYO`, `LUM TOKYO`, `NAA TOKYO`, `ZIP TOKYO`, `HONDA TOKYO` —
seven wrong answers next to the right one, and a wrong pick is a silently wrong
`bid_reduced`, not an error.

**Answer:** Ship `auction_aliases.csv` pre-filled with exactly six rows:
`U Tokyo→USS TOKYO`, `U Nagoya→USS NAGOYA`, `U Kyushu→USS KYUSHU`,
`U Osaka→USS OSAKA`, `U Yokohama→USS YOKOHAMA`, `Honda AA Tokyo→HONDA TOKYO`.
Confirm each, especially that banzai24's `U ` prefix is USS and not JU.



## Q27 — What keys a max bid, beyond make/model/year/mileage/rental?

Q13 fixed the columns, but the data says one `max_bid` row would cover **45
CX-30s that differ in two ways money cares about**: `modification` (trim —
"20S PROACTIVE TOURING" and others) and `grade_origin` (auction grade 3.5 vs 4.5
vs R). A CX-30 at grade 3.5 and one at 4.5 are not worth the same, and today they
would read the same `bid_reduced`.

**Answer:** Keep Q13's columns as answered — **trim and grade stay out of the key** —
because you can price the *worst* case per (model, year, mileage) and let the
grade badge on the card be the thing you eyeball. But say so explicitly, because
the alternative is a `grade_min` column now rather than a schema change later. If
you disagree on either, name which column you want.



## Q28 — Negative `bid_reduced`

`extra_costs` runs ¥4,000–¥47,000. A cheap car in Okinawa can make
`max_bid − extra_costs` zero or negative. Print the negative number, print ¥0, or
print null with a reason?

**Answer:** **Null with a reason** (`area cost ¥47,000 exceeds max bid ¥30,000`). A
negative number typed into a bidding platform is meaningless, and ¥0 reads like a
valid bid of nothing. This is the "don't buy this car at this house" signal and it
should look different from a price.



## Q29 — The exact set of null reasons

Q25 wants a test per reason, so the set has to be closed. Proposed, in evaluation
order:

1. `bid prices not loaded` — file absent (global, Q15)
2. `area prices not loaded` — file absent (global, Q15)
3. `unknown auction house: U Tokyo` — no alias, no fold match (Q21)
4. `sheet does not say rental or private` — no extraction, or both notes null
   (Q12/Q20) — **54 of 62 lots today**
5. `no table row for MAZDA CX-30 2023 · 15,000 km · private` — lookup miss (Q4)
6. `missing year` / `missing mileage` — lot field null (Q4)
7. `area cost ¥X exceeds max bid ¥Y` — Q28

**Answer:** These seven, first match wins, verbatim strings tested. Add or cut any.



## Q30 — "Two rows matching one lot = load-time error" — against what?

At load time there is no lot. What is actually detectable is **row overlap**: two
rows sharing (make, model, year, rental) whose mileage bands intersect. That is a
strictly stronger check — it catches a shadowed row even when no lot currently
falls in the overlap.

**Answer:** **Overlap check at load**, on the normalised (make, model, year, rental) key,
with open-ended bands treated as ±∞. Rows differing only in `rental` never
conflict. The error names both line numbers.



## Q31 — Where does the global "bid prices not loaded" reason appear?

Under Q15 an absent default file renders the report with a reason rather than
failing. The file now exists, so this fires only if it is moved or deleted — but
the reason still needs a home.

**Answer:**  **One line in the report header next to `cyprus_reason`**, plus per-card
silence — repeating "bid prices not loaded" 62 times is noise. The per-card
reasons in Q29 items 3–7 stay on the card.



## Q32 — How does the `bidrecord` table actually get dropped?

There is no migration framework; `init_db()` does `create_all` plus a
column-healing pass. Precedent is a standalone script
(`bazaraki/migrate_make_model.py`). Options: (a) a one-shot
`banzai24/migrate_drop_bidrecord.py` you run once, (b) `DROP TABLE IF EXISTS
bidrecord` inside `init_db()`, permanent.

**Answer:** **(a), a one-shot script.** 



## Q33 — How far into `AUCTION_PLAN.md` does the Phase 5 removal cut?

Beyond the Phase 5 section itself, `BidRecord` is documented at `:419` (storage
model) and `:609` (report flags), Phase 5 has a row in the Build-order table at
`:717`, and "Still open #1" is the bidding-site URL.

**Answer:** **All of it**: delete the Phase 5 section and its Build-order row, delete the
`BidRecord` class from the storage model, drop the `BidRecord` mention from the
Phase 4 flag description, delete "Still open #1", and rewrite "Still open #2" to
record that the report now computes `bid_reduced` while placing a bid remains
manual and unscoped. Confirm you want #1 gone rather than parked — the bidding
site URL is knowledge you'd otherwise re-derive.



## Q34 — `bidding.py`'s shape and the test seam

Q6 says "same shape as `CyprusPricer`". Concretely: a `BidPricer` holding both
tables, constructed once per report, `for_lot(lot, extraction) -> BidQuote(max_bid,
extra_costs, bid_reduced, reason)`; `LotView` gains `quote: BidQuote | None`;
`Report` gains a global reason field. `collect()` currently takes
`pricer: CyprusPricer | None = None` purely as a test seam.

**Answer:** Mirror it exactly: `collect(run_dir, all_lots=False, pricer=None,
bid_pricer=None)`, and `run_report`/CLI pass the two paths through. `BidQuote` is a
frozen dataclass with a `describe()` like `CyprusComp` — the Q16 money-block string
is formatted there, so `test_bidding.py` asserts on it without rendering, and
`test_report.py`'s two assertions just check it reached the page.

---

## Round 5 (blocked on the above)

- Alias-file failure modes (dangling target, duplicate `db_name`) — depends on Q26.
- Whether an explicitly-flagged path that does not exist is quiet or fatal —
  depends on Q15/Q22.
- Whether the money block renders at all for the 54 lots that hit Q29 reason #4 —
  depends on Q16/Q29.

---

# Round 5

## Facts established (looked up, not to be answered)

**Q26 is settled by evidence, not by a further answer.** The Q26 answer was the
recommendation copied verbatim, ending "Confirm each" — so the confirmation was
never given. Rather than ask a third time:

- The **raw API payload carries nothing more than `{"id": 39, "name": "U Tokyo"}`** —
  no fuller name, no code. `lots.json` cannot settle it.
- The **sheet scans are stored at 800×800 and lose the footer** where the auction
  house prints its own name. `50-1555-53023.jpg` (U Kyushu) shows a
  「プライムRV&Dコーナー」 header and no house name. Scans cannot settle it either.
- **The cost file's own structure does.** `USS` is the only house family among the
  123 rows holding all five `U ` cities — `USS TOKYO`, `USS NAGOYA`, `USS KYUSHU`,
  `USS OSAKA`, `USS YOKOHAMA`. `JU` has TOKYO but AICHI and KANAGAWA where `U `
  needs Nagoya and Yokohama; `NAA` has no KYUSHU and no YOKOHAMA; `NPS` and `LUM`
  cover neither. `TAA Kyushu` and `TAA Yokohama` already exist as separate DB
  houses that fold-match their own rows, so `U Kyushu` cannot be TAA. And
  `HONDA TOKYO` is the only `HONDA * TOKYO` in the file, for `Honda AA Tokyo`.
- A five-way coincidence that only USS satisfies. **The six aliases stand as
  written in Q26.** If you ever learn otherwise, it is one CSV row to fix — which
  is itself an argument for Q21's alias file over Q9's hard-coded dict.

New:

- **`bid_prices.csv` is staged; `auction_area_prices_2026.csv` is still untracked.**
  Both must be committed for the defaults in Q22 to work on a fresh clone.
- Q1 says `banzai24/bid_prices.csv`, Q22 says `banzai24/inputs/bid_prices.csv`.
  **Q22 wins** — later round, it matches where the cost file lives, and it is where
  the authored file actually is.
- **With the authored file, at most 7 of 62 lots can show a `bid_reduced`** — the 7
  with a `private_car_note` (Q20's accepted ceiling); the other 54 hit Q29 reason
  #4. Of those 7, only rows matching MAZDA CX-30 2023 under 60,000 km price at all;
  every TOYOTA RAV4 hits Q29 reason #5.

## Q35 — Starter `bid_prices.csv` — settled, no longer a question

You authored the file with real rows, so the "ship nothing / header-only / real
rows" choice is closed. Q29 reason #1 now fires only if the file is deleted or the
default path moves.


