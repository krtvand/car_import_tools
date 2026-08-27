# Auction statistics per search

Status: ready-for-agent

A page answering one question before you edit a `max_bid_jpy`: **what did the
cheapest cars I would actually have accepted sell for?**

Settled by grilling on 2026-08-26. Every decision below was put to the operator
and answered; the alternatives that lost are recorded where the reason matters.

## What it is

For each enabled search, for each **band**, up to **five concluded archive
lots** — the cheapest ones that pass that search's `[sheet]` requirements. Not a
distribution, not a median: five specific cars, each with a link, each vetted by
the same sheet extraction the morning report uses.

A histogram of all sales over three months was specified first and cut, to keep
this to one mechanism. The decision to cut it is the reason nothing here
accumulates a window of lots: see *Costs accepted*.

## The mechanism, and why it is cheap

Three facts about banzai24's archive, established by probing the live site on
2026-08-26. They are the whole design.

**1. `status=SOLD&source=archive` is the population.** A concluded lot carries
`endPrice`, `status.code`, `characteristics.modification`, `bodyModelCode`,
`lot.tradeDate`, `lot.auction.name` and an `auctImage` — everything a row needs.
All 75 SOLD lots across saved runs had a sheet, and sheet URLs are public
(`fetch.py`), so inspecting a concluded lot costs no session.

**2. More than half of sold lots hide their price — but not their rank.** Lots
tagged `Только на BANZAI24` come back with `endPrice: null` (54 of 99 for the
RAV4 filter; the correlation is perfect in both directions). The site reveals one
on click. **But the server sorts on the true price and only masks the display**:
requesting `sortPriceEnd=asc` and revealing every masked lot on page 1 gave

```
returned:  [None, 2960000, None, None, None, None, 3117000, 3135000, ...]
revealed:  [2885000, 2960000, 2970000, 3040000, 3065000, 3105000, 3117000, ...]
```

— twenty values, strictly ascending, no gaps. **The ranking is free; only the
number costs a click.** So the run never reveals a price it is not going to
print, and never sweeps a window to sort locally.

**3. The reveal cannot be shortcut, and fails silently if you try.**
`GET /api/catalog-service/lots/get-price/<lot-uuid>` →
`{"data":{"priceStart":2580000,"priceEnd":3695000}}`, but only when the SPA
makes the call. Both bypasses return **HTTP 200 with zeros**, not an error:

| how | result |
|---|---|
| clicking the button | real prices |
| `fetch()` from page context | `{"priceStart":0,"priceEnd":0}` |
| `httpx` + saved cookies | `{"priceStart":0,"priceEnd":0}` |

The endpoint needs the Authorization header the SPA adds from its in-memory
bearer token. This is exactly the arrangement `fetch.py` already commits to —
*let the page make its own authenticated calls and intercept the responses* — so
the reveal is a real button click, and **`priceEnd == 0` is a failed read, never
a price.** A lot whose reveal returns zero is dropped with a warning; storing it
would be a wrong page that still renders.

## The walk

Per band, in order:

1. Build the archive URL from this band and this search (below), sort it
   ascending by end price. Page 1 is the twenty cheapest lots in the window, in
   true price order.
2. Walk from the top. For each lot: download its sheet, run
   `banzai24.sheets`, judge it against `[sheet]` with the existing
   `requirements` machinery.
3. A lot that **meets all requirements** → click its reveal, store the price,
   keep it. A lot that fails or is unconfirmed → skip, reveal nothing.
4. Stop at five keepers, or at **20 inspections**, whichever comes first.

Never reveal a lot that failed. Never reveal a lot below the fifth keeper. A
second page is only fetched if the walk has not stopped by the end of the
first — and it often is, because `[api]` can reject a whole page without
spending anything. Broadening the RAV4 search to every trim line puts 565 lots
in the archive whose twenty cheapest are all petrol, so a hybrid-only `[api]`
walks six pages before its fifth keeper. Bounded at **25 pages**, reported when
hit, and the previous page's last price is carried forward as a floor so a page
turn that lost the sort is caught rather than silently becoming a different
list.

### The inspection cap

**20 sheet extractions per band per run.** Cheap lots are cheap because
something is wrong with them, and `[sheet]` rejects exactly what makes them
cheap — so the walk can run long on precisely the bands whose answer is least
useful. On hitting the cap the section renders what it found and says so:

> 20 inspected, 3 passed — the cheap end of this band is mostly damage.

That sentence is a finding about the band, not a failure of the page. The same
instinct as `dashboard/competitors.py`: **an empty list is never rendered as
good news.**

The cap barely binds after the first run. `db.extraction_is_current()` keys on
`lot_number` + `sheet_sha256`, so an already-inspected lot is free forever and a
later week pays only for lots that newly entered the cheap end.

## The search file

A new `[auction_statistics]` section. It **inherits `[site]` and `[api]`** and
declares only what differs — it is not a standalone filter set. `body_model_code`
stays in `[api]` and applies to both halves: a fact written twice in one file is
a fact that drifts, which is why the year and mileage bounds were pulled out of
`[site]` in the first place.

```toml
[auction_statistics]
model_grade = ["HYBRID G"]        # Модификация; new [site] key, matched as a substring
engine_capacity_start = 2.5       # any [site] key may be overridden here
```

Two values are **not** read from the file, because they are what a statistics
fetch *is* rather than facts about a car: `source = "archive"` and
`status = "SOLD"`. Year and mileage come from the band, as everywhere else.

New in `banzai24.config.AuctionFilters`: `status` and `model_grade`, mapped to
the `status` and `modelGrade` query parameters. `searches.definition._SECTIONS`
gains `auction_statistics`.

`model_grade` matches as a substring on the site's side — `HYBRID G` returns
modifications spelled `5D 4WD HYBRID G` and `HYBRID G 4WD`. `body_model_code` is
already substring-matched by `lot_filters`, which is what makes the bare
`AXAH54` and the prefixed `6AA-AXAH54` both match.

## Storage

Archive lots go in **`auctionlot`**, the table that already exists. It is keyed
by `lot_number` and already carries `status_code`, `end_price_jpy` and `source`;
putting them anywhere else would mean two spellings of "a lot" and a second
extraction cache. Sharing the table is what makes "don't inspect known lots
twice" fall out for free rather than being reimplemented.

**This changes what the buy-side readers see, and they must be audited.**
`db.pending_sheets()`, `db.lots_on()` and the morning report all read
`auctionlot` and currently assume every row is a lot you might bid on. Each gains
an explicit filter on `source` / `status_code`. A concluded archive lot appearing
in tomorrow morning's report would be a wrong report that still renders.

A revealed price is permanent: a concluded lot's hammer price never changes, so
`end_price_jpy` is written once and never re-revealed.

## Commands

| command | does |
|---|---|
| `python -m banzai24 stats --search NAME` | fetch, walk, inspect, reveal, store |
| `dashboard build` | renders `runs/auction_statistics.html` from the database |

The refresh needs a browser and a live session — the sort is a click and each
reveal is a click — so it cannot live in `dashboard build`, which promises to
need "two databases, a cost book and today's exchange rate" but never a session.
`daily.sh` runs `stats` for a search only when its stored data is older than
**7 days**, so "update the stat every week" is something the machine remembers.

## The page

`runs/auction_statistics.html`, its own page, linked from the runs index with a
one-line summary the way `competitors.html` is. One panel per enabled search,
one section per band, up to five rows.

Each row: **sale price ¥**, mileage, grade, `Модификация`, trade date, auction
house, the sheet thumbnail, and a link to the lot on banzai24.

**No comparison against a bid.** The page lists lots and nothing else. A
benchmark's `endPrice` is a hammer price and `max_bid_jpy` is an all-in maximum,
so putting them side by side would compare two different quantities and flatter
the bid by the size of the area price — and the right `extra_costs` differs per
row, because each lot sold at its own auction house. Deliberately deferred, not
forgotten: `bidding.py` already holds the line that a guessed `bid_reduced` is
worse than none.

## Validation

A bad search file fails **loudly, per search, and the others still render** —
the rule this repo already follows. An unknown key in `[auction_statistics]` is
an error, never a shrug.

Failure modes that must be visible rather than silent:

* a reveal returning `priceEnd == 0` — a failed read, dropped with a warning
* the cap reached with fewer than five keepers — say how many were inspected
* a band whose archive holds fewer than five sold lots at all — say so
* an expired session mid-walk — the existing `SessionExpired` path

## Costs accepted

**No histogram, and so no window.** Cut to keep this to one mechanism. The
consequence is that nothing accumulates: each run reads the cheap end fresh and
the page says nothing about how many cars sold, only what the cheap acceptable
ones went for. If the distribution is wanted later it is a second feature, and
it *would* need the three-month sweep this one avoids.

**The ranking is trusted, not verified.** The whole design rests on banzai24
sorting masked lots by their true price. That was measured, not assumed — but if
the site ever sorts nulls first, the walk silently inspects the wrong twenty
lots, and the page still renders five plausible cars that are not the cheapest
acceptable ones. Guarded: a revealed price lower than the previous keeper's
means the ordering has broken, and the run says so instead of finishing. See
`docs/adr/0006-a-masked-price-is-still-a-ranked-price.md`.

**Archive lots enter a table the morning report reads.** Mitigated by auditing
the three readers, but the risk is real and is the one decision here that is
hard to undo.

**The first run per band pays for up to 20 extractions.** Later weeks are nearly
free.
