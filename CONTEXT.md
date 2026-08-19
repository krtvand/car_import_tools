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
Cyprus. Context for a decision, never an input to a bid.
_Avoid_: Market price, resale value
