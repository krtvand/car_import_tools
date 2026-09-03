# A competitor is priced near you, not merely under you

A **competitor** is a Cyprus advert inside a band's declared competitor bounds,
passing the search's `[competitors]` filters, asking no more than the band's
**cyprus sell price** plus `price_ceiling_percent` — **5% unless the search says
otherwise** — live or gone within the last 90 days.

That last clause is the **ceiling**, and it is the whole of this decision. It
moves the line ADR 0007 drew at the sell price exactly, and it keeps the line
that ADR had — a competitor list is a list you can read, not the market dumped
on a page.

## Why the line is not at the sell price

`docs/adr/0007-competitors-are-a-queue.md` excluded every advert asking more
than the sell price: you hold the better offer, so it was never ahead of you in
the queue. True, and it cost the page its best evidence. On the RAV4 band on
2026-09-03 a 2022 car asked €29,900 — €134 more than the sell price — and was
gone in 30 days. That single row is the difference between *"I am next in the
queue"* and *"the market ends below me"*, and the old rule could not print it.

It is not an edge case, it is how buying works. A buyer weighing a €29,766 car
against a €29,900 one is weighing the same offer; the €134 is haggling room, not
a different market. So the line belongs a little above the price you must get,
and a **percentage** is how you say "a little" about cars that cost €18,000 and
€30,000 in the same repo — 5% is €900 on a CX-30 and €1,500 on a Harrier.

## Why the line is not removed

It was, for one afternoon, and it was wrong. Dropping the price test entirely
made a competitor "any in-bounds advert", which is a defensible sentence and an
unreadable page: the CX-30 bands went from 7 rows to 168, the enabled searches
from 20 competitors to 701, and the three rows that decide whether to bid sat
above 160 that do not. ADR 0007 predicted exactly this ("it would bury the three
rows that matter under 181 that do not") and it was right.

The operator's actual question — *how hard is this car to sell at my price* — is
answered by the adverts **near** that price, above and below it. Everything
further up is a different market. It is still counted, never listed:
`_stuck_above` reports live adverts past the ceiling that have not moved in
`OVERPRICED_AFTER_DAYS`, one line under the table.

## What follows from it

**The ceiling is measured from the sell price, and the count from the ceiling.**
The table and the stuck-above count meet exactly there, so no advert is ever
described twice or dropped between them.

**The euro on each row is signed.** `versus_sell_price` is
`cyprus sell price − advert price`: positive undercuts you, negative is the
headroom above you, and it never exceeds the ceiling because past that there is
no row. `CompetitorRow.undercuts` is the old membership test, kept as one
boolean per row.

**Two counts per band, never one.** The competitors, and how many of them ask
less than the sell price. "14 competitors" and "0 asking less" are the same
Harrier band, and either number alone is read as the other.

**The ceiling is printed on the page.** A list that stops somewhere unstated is
read as the whole market — the same reason a run the index cannot open is dimmed
rather than hidden.

**The marks are unchanged, and the reader supplies the side.** Red still means
the market refused that price for 30 days. Under your sell price it is a car you
are waiting behind; in the headroom above it, it is the market declining to pay
roughly what you need. The legend says so rather than splitting the palette.

**It is one filter, but not in `_passes`.** Every other `[competitors]` value can
be checked against an advert on its own; this one needs the band's price. So it
lives on `CompetitorFilters` as `price_ceiling_percent`, is resolved by
`CompetitorFilters.ceiling(sell_price)`, and is applied by the dashboard where
the sell price is known.

**Search-wide, not per band.** A band may narrow its bounds and its
`exclude_phrases`; the ceiling is one policy about how far you look, and every
band applies it to its own sell price. If a search ever needs two, that is a new
key and not a widening of this one.

**A negative percentage is refused.** It would mean a competitor has to undercut
you by a margin before it counts, which is a different idea and should be
written as one.

**A band that cannot be priced still lists nothing.** No sell price, no ceiling,
no membership test — the section says what is missing instead, exactly as before.
