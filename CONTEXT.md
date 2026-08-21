# Japanese Auction Sourcing

Finding cars at Japanese auctions that are worth importing to Cyprus. One
morning run turns a saved search into a page of cars, each judged against a
list you wrote and priced against what the same car asks for in Cyprus.

## Language

### The things being bought

**Lot**:
One car offered at one auction house on one day. Identified by the auction's own
lot number, which survives the car being re-listed later.
_Avoid_: Listing, item, vehicle

**Auction sheet**:
The inspector's one-page report on a lot — grades, the damage diagram, mileage,
車歴, 車検, and free-text notes. The half of a lot the list API does not carry.
_Avoid_: Inspection report, condition report, auction report

**Lot photo**:
One of banzai24's photographs of the car itself, as distinct from the scan of
the auction sheet. A lot carries a dozen or more; a run downloads the first few
and the report shows them in one row under the sheet, with the rest a click
away on the lot page. Context for the eye, never an input to a verdict — no
requirement is judged against a photograph.
_Avoid_: Image (the sheet is an image too), picture, thumbnail

**Damage mark**:
One code placed on the sheet's car diagram, e.g. `A1` on the roof. The letter is
the damage type, the digit its severity. Codes combine (`A3U2`) and are
sometimes written in Japanese (`トビA`).
_Avoid_: Defect, damage code (the *code* is the letter; the *mark* is the code
placed on a panel)

**車歴**:
Whether the sheet says the car was privately owned, ex-rental, ex-lease and so
on. A price input, not a requirement — an unreadable box is priced as private.
_Avoid_: History, ownership, usage

**Model spec**:
The manufacturer's figures for one model over a span of years — body length,
width, height, CO₂. True of every car of that model, never of one lot: a lot is
one car on one day, a model spec is the catalogue behind it. Only the dimensions
are read today; they decide shipping volume, and so the freight half of a landed
cost.
_Avoid_: Dimensions, car spec, vehicle data, model (which is a string on a lot)

### Deciding what to look at

**Search definition**:
One TOML file naming one car and everything wanted from it. The complete
declaration — nothing is inherited from anywhere else.
_Avoid_: Config, filters, saved search, profile

**Requirement**:
One condition a lot must satisfy, declared in a search definition. Every
condition in the file is a requirement; the section it sits in says who is able
to check it, not how much it matters.
_Avoid_: Filter, criterion, rule

**Site requirement** (`[site]`):
A requirement banzai24 can express in its own search, so lots failing it never
reach us — and which the auction sheet then re-judges more precisely.

**API requirement** (`[api]`):
A requirement we check ourselves against list data, before any sheet is read.
Lots failing it are dropped and never appear on a report.

**Sheet requirement** (`[sheet]`):
A requirement only the auction sheet can answer. The only kind that can put a
lot in *fails a requirement*.

### The verdict

**Meets all requirements**:
The sheet was read and nothing on it disqualifies the car.
_Avoid_: Approved, passed, clean, buy

**Unconfirmed**:
No answer yet — the sheet is unread, its extraction failed, or the field a
requirement needed was blank on an otherwise-readable sheet.
_Avoid_: Unchecked, pending, unknown

**Fails a requirement**:
The sheet was read and something on it disqualifies the car.
_Avoid_: Rejected, failed, excluded

**Mismatch**:
The sheet and the list API disagree about a fact no requirement tests — the
chassis number or the registration date. A sign the lot may not be the car the
listing describes, which is a separate concern from whether it is wanted.

### Money

**Max bid**:
The all-in maximum for a car, in JPY, read off an operator-authored table keyed
by make, model, year, mileage band and 車歴.

**Extra costs**:
The auction house's area price. The only thing subtracted from a max bid.
_Avoid_: Fees, area cost, transport

**Bid reduced**:
`max bid − extra costs` — the number typed into the bidding platform.
_Avoid_: Final bid, our bid, target price

**Cyprus comparable**:
The median asking price for the same make, model, year and mileage band in
Cyprus. Context for a decision, never an input to a bid — it still sets no
number you type anywhere. It now also sits beside a *margin*, which is a thing
you read rather than a thing that bids; the day a comparable starts choosing a
max bid, this line stops being true and should be rewritten rather than quietly
stretched.
_Avoid_: Market price, resale value

**Landed cost**:
Everything paid between the hammer falling in Japan and the car standing on
Cyprus plates — auction price, exporter fees, freight, VAT, and the fixed bills
at this end. Your profit is not in it. Ported from the operator's spreadsheet;
see `price_calculator/price_calculator_spec.md`.
_Avoid_: Total cost, import price, all-in (which is what a *max bid* is, at the
auction and in yen)

**Cyprus estimate**:
What the same car would realistically *sell* for here — the fitted asking curve
with a resale haircut on it. A different claim from a **Cyprus comparable**,
which is what cars are being *asked* for: one is the market's opening position,
the other a guess at where it closes.
_Avoid_: Sale price, market value, resale price

**Margin**:
`Cyprus estimate − landed cost − resale costs`, in euro and as a percentage of
the landed cost. What the car is expected to earn. Never an input to anything —
it is the number the page exists to show you.
_Avoid_: Profit, spread, ROI
