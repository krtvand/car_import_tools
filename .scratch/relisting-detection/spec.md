# Relisting detection

Status: needs-triage

A delisted advert is treated everywhere in this repo as a **sold-proxy**. Some of
them are not sales at all: the seller pulled the advert and posted the same car
again, usually cheaper. Those cars never left the market, and counting them as
sales tells you the market is liquid at a price where it is actually stuck.

Found while grilling the competitor marks (2026-08-28), which is where it bites
hardest: a relisted car earns the mark meaning *"this price is fair, the car
sells"* while being evidence of the exact opposite.

## The evidence

Delisted adverts matched against later adverts with the same title, year and
mileage, excluding near-zero-mileage rows (identical dealer stock, not relists):

```
gone €23,900 (5 Aug)  →  back 22 Aug as €23,500   Mazda CX-5   35,000 km
gone €39,500 (12 Aug) →  back 21 Aug as €38,500   Toyota RAV4   5,000 km
gone €23,000 (6 Aug)  →  back 21 Aug as €22,999   Mazda CX-30  14,500 km
gone €24,000 (5 Aug)  →  back 18 Aug as €24,900   Mazda CX-5   34,000 km
gone €21,900 (1 Aug)  →  back  1 Aug as €20,200   Mazda CX-30  35,000 km
```

11 clean pairs across the four searched cars. €23,000 returning as €22,999 is
not a car that sold.

## What is affected

Everything that reads delisting as a sale:

* `dashboard/competitors.py` — the mark for a competitor gone within the window,
  and `Sold` / `SOLD_WINDOW_DAYS`
* `bazaraki/analysis.py` — `survivorship_adjustment` splits on `is_active` and
  `days_on_market` to derive the asking→sale haircut, so relists inflate the
  "sold fast" group with cars that did not sell, at prices they failed to sell at
* `days_on_market` itself — a relisted car's clock restarts from zero on the new
  `ad_id`, so a car six months on the market reads as new

That third one matters most: relisting defeats age measurement even after Q7's
`min(published, first_seen_at)` clamp, because the new advert is genuinely a new
row with a genuinely recent publish date.

## Open questions

* What identifies "the same car"? Title + year + mileage is what found the
  evidence above, but mileage is seller-entered and may be updated on the relist,
  and title is free text. Photo URL or `image_url` hash may be stronger.
* Once matched, is a relist a new advert that inherits the original's
  `first_seen_at`, or a distinct row linked to its predecessor? The first keeps
  `days_on_market` honest with no new concept; the second keeps the price
  trajectory across relists visible, which is what `PriceObservation` exists for.
* Does a matched relist prove the original did not sell, or only that it probably
  did not? A dealer with two identical cars is indistinguishable from a relist on
  the fields above, which is why near-zero mileage had to be excluded by hand.

## Not in scope

Excluding relists from the competitor table's marks. Deliberately deferred on
2026-08-28: the competitor table ships with relists unhandled, and this is the
task that fixes it there and everywhere else at once.
