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
One TOML file naming one car and everything wanted from it — in Japan and in
Cyprus. The complete declaration: nothing is inherited from anywhere else, and
both parsers read this one file. Made of *bands*.
_Avoid_: Config, filters, saved search, profile

**Band**:
One row of a search's bid table: a year, a mileage range, and the max bid for it
per 車歴. What a search is actually made of — the search's own fetch bounds are
the union of its bands, never written separately, because a bound written beside
a price drifts from it.
_Avoid_: Tier, block, range, row

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
The all-in maximum for a car, in JPY, read off the *band* it falls in. Operator-
authored, re-tuned often, and read live rather than from the run — so
re-rendering an old report re-prices it. See
`docs/adr/0004-bid-prices-are-read-live.md`.

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
at this end. Your profit is not in it. The arithmetic is a port of the operator's
spreadsheet (`price_calculator/price_calculator_spec.md`); the prices it runs on
are a **cost book**.
_Avoid_: Total cost, import price, all-in (which is what a *max bid* is, at the
auction and in yen)

**Cost book**:
Every price in force on a date that is not a property of one car — exporter
fees, the RoRo rate, VAT, the FX haircut, the fixed Cyprus bills. Kept as a
file, edited when a supplier or the state changes a price, never when the
arithmetic changes. Stamped onto a run, so re-opening an old report shows the
prices that priced it rather than today's.
_Avoid_: Fees (which are *extra costs*), constants, config, rates (which is FX,
and a different thing)

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

**Cyprus sell price**:
`landed cost + resale costs + the profit required from this car`. What you
**must** get. The same arithmetic as a *margin* run backwards: a margin is read
off the market, this is declared and the market judged against it.
_Avoid_: Sell price, asking price — and above all not a **Cyprus estimate**,
which is what the market says you **would** get. The two disagreeing is the
point: when the required price is above the estimate, the car does not work at
any profit, and no competitor has to do anything for that to be true.

**Competitor**:
One Cyprus advert, inside a band's declared competitor bounds, asking less than
that band's *cyprus sell price* — live, or gone from the market within the last
three months. Deliberately not the same car: an older one, or one with more
kilometres on it, still takes the sale. A car that sold for *more* than your
sell price is not a competitor at all, because you hold the better offer. Not a
**Cyprus comparable**, which is a median over similar cars and describes a
market — a competitor is one advert, with a link, that undercuts you
specifically.
_Avoid_: Rival, listing, comp

**Advert age**:
How long one Cyprus advert has been on sale — from the day it was published to
the day it left the market, or to today if it is still up. The axis every
judgement about a competitor hangs on: gone quickly means the price worked, and
still sitting means it did not. Measured from the *earliest* date the advert can
be shown to have existed, because sellers re-publish a stale advert to lift it
up the results page, and a bumped advert must never come out younger.
_Avoid_: Days listed, time on market, freshness

**The queue**:
The order buyers work through a market: the cheapest acceptable car first, then
the next. A *competitor* is one car ahead of you in it. What a competitor's
**advert age** tells you is therefore about your own wait, not only about them —
cars ahead of you leaving quickly means the queue is moving and your turn comes
later rather than never, while cars ahead of you sitting still means it is not
moving at all. The reason the same fact — an advert gone from the market — is
read as good news here and as a mere *sold-proxy* elsewhere.
_Avoid_: Pipeline, funnel, market position

**Auction statistics**:
The five cheapest concluded lots that would have met every requirement in a
search — per band, each one a car with a link, not a summary of a market. Named
for the site feature it reads rather than for what it is: it counts nothing and
averages nothing, and the day it grows a distribution this line should be split
rather than stretched. The Japan-side counterpart to a **competitor**, and just
as deliberately individual.
_Avoid_: Stats, price history, sold comparables — and not a **Cyprus
comparable**, which is a median

**Hidden price**:
The hammer price of a lot banzai24 sells through its own channel, masked in the
list and shown one click at a time. Hidden from display only: the site still
*orders* by it, so a masked lot's position among the others is its true rank.
A hidden price read back as zero is a failed read, never a free car.
_Avoid_: Missing price, null price, unavailable price
