# Competitor marks: how long each competitor took to leave

Status: ready-for-agent

The competitors table shows live adverts only, so a competitor that sold
vanishes from the page — taking with it the most useful thing it could tell you.
This adds gone competitors back, and colours every row by how long it took to
go.

Settled by grilling on 2026-08-28. Every decision below was put to the operator
and answered; the alternatives that lost are recorded where the reason matters.

## The model

Buyers work a market cheapest-acceptable-first. A **competitor** is one car
ahead of you in that queue. How long it took to leave the queue is evidence
about your own price:

| | age ≤ 30 days | age > 30 days |
| --- | --- | --- |
| **gone** | green — sold fast, the price is fair | red — took too long, the price is too high |
| **live** | neutral — no verdict yet | red — sitting unsold, the price is too high |

One threshold, `OVERPRICED_AFTER_DAYS = 30`, applied to one number. It collapses
to: *age over 30 days is red; otherwise gone is green and live is neutral.*

**Advert age** is publish date → delisting, or → today if still live.

## Decisions

### Which adverts appear

Unchanged from today except for the gone ones: inside the band's
`[band.competitors]` bounds, passing the `[competitors]` filters, **priced under
the band's cyprus sell price**. Live adverts regardless of age; gone adverts
whose `delisted_at` is inside `COMPETITOR_HISTORY_DAYS = 90`.

A car that sold *above* your sell price is not shown. Rejected deliberately: the
operator holds the better offer, so it is not a competitor. This does mean the
table cannot tell you whether the market ends below you — see
`.scratch/market-state-panel/spec.md`, which is the task for that.

Status: **done**, and the stuck-above line ships with it.

Green does not fade with age. A car that sold in 9 days looks the same whether
it went last week or in June; a fast sale is evidence about the price whenever
it happened.

### Advert age starts at the earliest provable date

`min(published date, first_seen_at)`, never `posted_raw` alone.

`posted_raw` is a **bump** date, not a publish date. 95 of 218 RAV4 adverts (44%)
claim a publish date later than the day we first saw them, and on 2026-06-24
adverts 5589703 and 6571409 both claimed "posted today" while sitting a million
sequential ad_ids apart. Sellers re-publish stale adverts to climb the results
page — so parsing `posted_raw` alone paints the most desperate adverts freshest,
inverting the signal. Clamping with `min` keeps what the field is good for: real
publish dates reaching back before the crawl began (the `DD.MM.YYYY` rows go to
27 May), on 79 of 133 in-bounds RAV4 rows and 86 of 159 CX-30 ones.

Relative forms anchor to **`last_seen_at`**, not `first_seen_at`: an upsert
overwrites `posted_raw` every crawl, so "3 weeks ago" is three weeks before the
most recent sighting. Anchoring at the first sighting back-dates every advert by
however long it has been watched — the error that produced the "50 stuck above"
figure quoted during grilling, whose true value for that band is 0.

### Where the age calculation lives

In `dashboard/`, not `bazaraki/models.py`. The existing
`CarListing.days_on_market` counts from `first_seen_at`; fixing it there would
also change `bazaraki.analysis.survivorship_adjustment`, which splits on it to
derive the asking→sale haircut — so every Cyprus estimate on the page would
silently re-price as a side effect of a colour change. The divergence is
deliberate and should be noted in a docstring.

Likewise the constants are dashboard-local: no importing `FAST_DAYS` from
`bazaraki.analysis`, even though it is also 30. *How long before a price is too
high* and *what counts as a fast sale for a haircut* are different questions
that happen to share a value today.

### Presentation

* Colour lands on the **`days up` cell**, not the row: the number in it is the
  reason for the colour, so the distinction survives in greyscale.
* No short labels on the marks — a legend under the table carries the reading.
* Rows stay **cheapest-first**. That is the order buyers work through, so it is
  the order the queue is in.
* Delete the `Sold` / `Sold.describe()` line. It counts delisted adverts
  regardless of price, so it now contradicts the rows above it.
* Add one line under the table: *"N adverts above your price have been listed
  over 30 days."* No extra rows. Without it the table can read as an ordinary
  market while the band just above it is frozen — the CX-30 2023 band shows 7
  rows against 29 stuck above. (For the RAV4 the count is 0: its crawl is 19 days
  deep and no in-bounds advert reports a publish date older than 30 days.)
  Superseded later by the market state panel.

## Prerequisite: backfill the flushed delisting dates

37 adverts carry `delisted_at = 2026-08-25 19:01:48`, the backlog flush from
`859c58c` (the first run after delisting began working for make-prefixed model
slugs). Their true exits spanned 09–23 Aug.

This must be fixed before the marks ship, because the fabricated date sets the
colour and it is always later than the truth, so it always paints fast cars
slow. **16 of the 37 change mark — every one red → green** — including the
advert that prompted this work:

```
ad 6631913   published 24 Jul   last seen 9 Aug   flush-stamped 25 Aug
   shown:  red,   32 days  → "took too long, your price is too high"
   truth:  green, 16 days  → "sold in a fortnight, the price is fair"
```

Fix: `UPDATE carlisting SET delisted_at = last_seen_at WHERE delisted_at = <flush
timestamp>`. The true dates are already stored. Back up `bazaraki.db` first.

## Known gap, deliberately shipped

Relisting is not handled. Roughly one delisting in four is a seller who pulled
the advert and posted the same car cheaper; each one earns a green mark while
meaning the opposite. See `.scratch/relisting-detection/spec.md`.

## Glossary

`CONTEXT.md` gains **Advert age** and **The queue**, and **Competitor** is no
longer live-only.
