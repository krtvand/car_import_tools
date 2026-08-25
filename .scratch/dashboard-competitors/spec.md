# Dashboard: competitors per search

Status: ready-for-agent

A web page answering one question every morning: **for each car I am buying,
what is already on sale in Cyprus for less than I would have to charge?**

Settled by grilling on 2026-08-24. Every decision below was put to the operator
and answered; the alternatives that lost are recorded where the reason matters.

## The shape of it

`searches/` becomes the single declaration of one car's whole operation — what
to buy it for in Japan, and who undercuts you selling it in Cyprus. Both parsers
read it. `dashboard/` renders it. `banzai24/inputs/bid_prices.csv` and
`bazaraki/searches/*.sh` cease to exist.

### The merged search file

```toml
# searches/mazda-cx30.toml
car = "mazda-cx30"                    # identity; each parser translates it itself

[site]                                # no year/mileage — derived from [[band]]
transmission = "auto"
engine_capacity_start = 1.9
grade = ["4", "4.5", "5"]
source = "auctions"

[api]
body_model_code = ["DMEJ3R", "DMFP", "DMEJ3P"]
exclude_colours = ["black", "blue"]

[sheet]
drivetrain = "2WD"
no_damage_codes = ["W", "X", "欠"]

[dashboard]
enabled = true                        # absent section ⇒ enabled
expected_profit_eur = 2000

[competitors]                         # common; applied in memory, not at scrape
fuel_type = ["Petrol", "Hybrid Petrol"]
engine_size_start = "1,8L"
engine_size_end = "2,5L"

[[band]]
year = 2023
mileage_start = 0
mileage_end = 50000
max_bid_jpy = { private = 1_805_000 }   # a missing rental key falls back to private
  [band.competitors]
  year_start = 2019
  mileage_end = 120000
```

The `[[band]]` list is `bid_prices.csv` moved into the file, and it is what a
search is *made of*: the site's year and mileage bounds are the union of the
bands, never written by hand. Two of the four searches were already drifting
from the CSV when this was written — the CX-30 fetched to 55,000 km while the
table priced to 60,000, and the CX-5 fetched to 70,000 km with no bid price
above 60,000 — which is the whole argument for the merge.

`[competitors]` at the top level is common to every band and applies to all of
them; `[band.competitors]` bounds the years and mileage that count as
competition for that one band.

### Modules

```
searches/     cars.py        Car: key, make, model — canonical only
              definition.py  SearchDefinition, Band, parse, validate
              cli.py         list, check
              *.toml
              → imports nothing
banzai24/     + cars.py      key → its own site vocabulary
bazaraki/     + cars.py      key → URL slugs; the RAV4 404 note lives here
dashboard/    index.py, competitors.py, templates/, cli.py
              → imports searches, banzai24, bazaraki, price_calculator
```

A URL slug is bazaraki's private business, so it lives in bazaraki. `searches`
knows only that a car exists and what it is called; it cannot ask a parser
whether it knows a slug, so a missing one surfaces at `bazaraki scrape` or
`dashboard build` rather than at `searches check`.

## How one band becomes a panel

1. `max_bid_jpy.private` → `price_calculator` → **landed cost**, at *today's* FX
   and today's `costs.toml`, with the newest run's stamps as an offline
   fallback. The rate's timestamp is shown on the page.
2. **cyprus sell price** = landed cost + resale costs + `expected_profit_eur`
   (a band-level override wins over the search-level default).
3. **Cyprus estimate** beside it. If the required price is above the estimate,
   the band does not work at any profit and the competitor list is a footnote.
4. **Competitors**: active, non-`IN_TRANSIT` bazaraki adverts for this car,
   passing `[competitors]` and this band's `[band.competitors]`, asking **less**
   than the cyprus sell price. Sorted by price ascending — the cheapest undercut
   is the one that costs the sale. Each row: price, the € gap, year, mileage,
   fuel, gearbox, seller type, days on market, link. Beneath the list, one line
   of context: *N sold in the last 30 days, median €X*.

An advert may be a competitor of one band and not another, and may appear under
both. That is not duplication — the bands are asking different questions.

## Commands

| command | does |
|---|---|
| `python -m searches list` / `check` | describe / validate every search (`banzai24 searches` aliases it) |
| `bazaraki scrape --search mazda-cx30` | one crawl, one `ScrapeRun`, scope = union of the bands' competitor bounds |
| `dashboard build` | writes `runs/index.html` + `runs/competitors.html` |
| `dashboard open` | build, then `session.review()` in the parser's own Chrome |
| `banzai24 report --open` | opens `runs/index.html` without writing it |

`daily.sh` (moved to the repo root): check → fetch → `extract --today` →
`report --today` → bazaraki scrape per search (unless `--no-cyprus`) →
`dashboard build`.

## Validation

A bad search file fails **loudly, per search, and the others still render**.
`dashboard build` gives the broken one a panel naming the wrong line;
`banzai24 fetch --search X` on a broken file raises and stops, because fetching
against a half-parsed definition spends money on the wrong lots.

Load errors: overlapping bands; a `[band.competitors]` narrower than the band
itself (which would exclude the car's own mileage from its own competitor list);
unknown keys; a search with no bands. An enabled search missing `[competitors]`
or `expected_profit_eur` renders a panel saying what is missing — **never an
empty list**, because with `enabled` defaulting to true a typo would otherwise
be indistinguishable from a car nobody undercuts.

## Deleted

`banzai24/inputs/bid_prices.csv` · `bazaraki/searches/*.sh` ·
`bazaraki.config.DEFAULT_FILTERS` and `--no-defaults`, which existed only to
defend against it · `banzai24/index.py` and its template, moved to `dashboard/` ·
`banzai24/searches/*.toml`, moved to `searches/` · `daily.sh`, moved to the root
now that it drives three modules and its directory is empty.

## Costs accepted

**Old reports re-price at today's bids.** The bid table is read live, not from
the run — `search.for_run` already prefers the named file so a re-tuned
requirement re-judges an old morning for free, and bids now ride along. August's
report, re-rendered in September, no longer shows what you bid that morning.
Costs and FX keep stamping: those change under you without your knowing, whereas
a max bid changes because you changed it. The bands are still *recorded* in
`lots.json` provenance as an audit trail; they are simply not what renders.
See ADR 0004.

**Deriving fetch bounds changes what tomorrow's run fetches.** CX-30 widens
55,000 → 60,000 km. CX-5 gains 0–30,000 km and loses 60,001–70,000 km, which is
currently fetched and has never had a bid price. Migration puts that choice in
front of the operator per car.

**The first wide Cyprus crawl will delist a lot.** 52 CX-30 adverts are
currently frozen `is_active` outside every recent scrape's scope, because
`db._in_scope` bounds delisting to the run's own scope. Widening clears them —
but only on a *completed* run, so `--max-pages` has to be raised first.

**Three of four searches have no bands or profit yet** and will render
"not configured" panels until filled in.
