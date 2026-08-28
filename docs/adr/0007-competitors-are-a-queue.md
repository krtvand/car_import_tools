# Competitors are a queue

The competitors table colours each row by how long the advert took to leave the
market: gone within 30 days is good news, still listed after 30 days is a
warning. That is backwards from how the rest of this repo reads a delisting, and
the reason is the queue.

Buyers work a market cheapest-acceptable-first. A **competitor** is one car
ahead of you in that order. So a competitor's *advert age* is not a fact about
them, it is a fact about your own wait: cars ahead of you leaving quickly means
the queue is moving and your turn comes later rather than never, while cars
ahead of you sitting unsold means it is not moving at all. An advert
disappearing is therefore the friendly colour, and one that has been up for six
weeks is the alarming one.

Elsewhere the same event is only a **sold-proxy** — a weak signal feeding the
asking→sale haircut in `bazaraki.analysis`. Here it is read as evidence about a
price. Both readings are deliberate, and this is the file that says so.

## What a competitor is not

A car that sold for *more* than the band's cyprus sell price is **not** a
competitor and is not shown, however fast it went. You hold the better offer; it
was never ahead of you in the queue.

This was the sharpest trade-off in the design, because it costs something real.
For the Mazda CX-30 2023 band on 2026-08-28:

```
under the €18,822 sell price:                    7 adverts   ← the whole table
in-bounds, live, listed over 30 days, above it:      29
```

Twenty-nine cars have sat unsold just above that price for over a month. None
can ever appear as a row, so without saying so the table shows seven rows and
reads as an ordinary market while the price band just above it is frozen. **The
table cannot tell you whether the market ends below you.**

Widening it to all in-bounds adverts was rejected: it would bury the three rows
that matter under 181 that do not, and it would cost the word "competitor" the
only precise meaning it has. The gap is answered instead by a separate market
state panel (`.scratch/market-state-panel/spec.md`), with a one-line footnote
under the table until that exists.

## Age is measured from the earliest provable date

`min(published date, first seen)`, never the published date alone.

bazaraki's published date is a **bump** date. Sellers re-publish a stale advert
to lift it up the results page, and the site then reports it as newly posted: 95
of 218 RAV4 adverts claim a publish date later than the day we first recorded
them, and on 2026-06-24 adverts 5589703 and 6571409 both claimed "posted today"
while sitting a million sequential ad_ids apart.

The relative forms are read against ``last_seen_at``, not ``first_seen_at``: an
upsert overwrites ``posted_raw`` every crawl, so "3 weeks ago" is three weeks
before the *most recent* sighting. Anchoring it at the first sighting instead
back-dates every advert by however long it has been watched, and inflates the
ages this page is built on.

Trusting that field would not merely add noise, it would invert the signal —
the seller who cannot sell is precisely the seller who bumps, so the most
desperate adverts would come out freshest and earn the neutral colour while
honest untouched adverts aged into the warning. Clamping with `min` keeps what
the field is genuinely good for, real publish dates reaching back before the
crawl began, on 79 of 133 in-bounds RAV4 rows and 86 of 159 CX-30 ones.

## Consequences

**The age calculation is dashboard-local.** `CarListing.days_on_market` counts
from `first_seen_at` and is left alone: `bazaraki.analysis.survivorship_adjustment`
splits on it to derive the asking→sale haircut, so correcting it there would
silently re-price every Cyprus estimate on the page as a side effect of a colour
change. Two notions of an advert's age now exist in the repo, deliberately.

**The thresholds are duplicated, not shared.** `OVERPRICED_AFTER_DAYS` is 30 and
`bazaraki.analysis.FAST_DAYS` is 30, and they are not the same constant. *How
long before a price is too high* and *what counts as a fast sale for a haircut*
are different questions that happen to agree today; fusing them would mean
tuning one silently moves the other.

**Relisting is unhandled and the marks are wrong because of it.** Roughly one
delisting in four across the searched cars is a seller who pulled the advert and
posted the same car cheaper — €23,000 returning as €22,999. Each earns the green
mark asserting the price was fair, while being proof of the opposite. See
`.scratch/relisting-detection/spec.md`. This is the largest known defect in the
reading above, and it is shipped knowingly.
