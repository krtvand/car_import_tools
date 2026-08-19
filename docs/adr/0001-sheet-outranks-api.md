# The auction sheet outranks the list API

Where the sheet and the list API both carry a fact — mileage, grade, registration
date — the **sheet's value is used everywhere**: to judge requirements, and to
look up the max bid. This reverses the precedence recorded in
`BID_PRICING_QUESTIONS.md` Q3/Q14, where the API won and the sheet only filled
nulls.

## Why

The API rounds mileage to the nearest 1,000; the sheet prints it to the
kilometre. Every extraction in `auction.db` differs from its API figure, by −782
to +415 km. Under the old precedence a car whose sheet reads 50,415 km and whose
API row reads 50,000 km is priced from the *under-50,000* band — ¥150,000 too
high against the shipped table — while a requirement of `mileage_end = 50000`
would pass it. Judging the requirement on one number and the money on another is
the same bug twice.

The sheet is also the document the API's own values are derived from, so
preferring it is not a coin-flip between two sources; it is preferring the
original over a rounded copy.

## Consequences

**Nothing changes today, which is exactly the danger.** No lot in `auction.db`
sits within 1,000 km of a bid-table band edge, so flipping this alters no
current bid and no current verdict. A future reader benchmarking the two
precedences will measure no difference and may flip it back. The cost of that is
silent: a wrong bid on one car at one band edge, on a report that still renders.

The API keeps winning where the sheet is null, and the sheet's own nulls are
never treated as zero — an unreadable mileage box leaves the lot *unconfirmed*
rather than priced from a guess.
