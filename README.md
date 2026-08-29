# bazaraki-scraper

Scrapes car listings from [bazaraki.com](https://www.bazaraki.com/) into a SQLite
database and exports them to an Excel/Numbers-compatible `.xlsx` file.

Built with [Crawlee for Python](https://crawlee.dev/python/) (BeautifulSoup
crawler). Crawlee's realistic browser headers get past the site's 403 bot block;
it also handles retries, throttling and the request queue.

## Setup

```bash
uv sync
```

## Search filters

What to scrape comes from a **saved search** — `searches/<name>.toml`, the same
file the auction side runs on. `bazaraki scrape --search mazda-cx30` crawls that
car over the union of its bands' competitor bounds, which is exactly the range
the dashboard's panels ask about:

```bash
uv run python -m bazaraki scrape --search mazda-cx30
```

That is deliberate rather than convenient. `db._in_scope` bounds delisting to the
scope of the run that found an advert, so an advert outside every recent crawl
keeps its "still on sale" flag for ever — and a panel whose scope and whose crawl
were declared separately produces false competitors. See
`docs/adr/0005-one-search-file-two-parsers.md`.

For a one-off probe — checking a model slug, seeing how much stock a bound pulls
in — the individual flags still work, and apply only what you typed. They cannot
be combined with `--search`.

The `CarFilters` dataclass in `bazaraki/config.py` lists every available site
filter. Available fields: `make`, `model`, `price_min/max`, `year_min/max`,
`mileage_min/max`, `engine_size_min/max` (e.g. `"1,6L"`), `gearbox`
(`"Automatic"`/`"Manual"`), `fuel_type` (`"Petrol"`, `"Diesel"`, …), `drive`,
`doors`, plus multi-select `body_type` / `colour` / `seats` / `extras`, and
`q` (free-text). `make`/`model` are the URL slugs — to find a model's slug,
open its page on bazaraki and read the last path segment (e.g. `mazda-6`, `323`).

Filter values are human-friendly: ranges use real values (`price_max=25000`),
enumerations use labels (`fuel_type="Petrol"`). Year/engine-size codes are
resolved automatically from the live page before the crawl.

## Usage

```bash
uv run python -m bazaraki scrape --search mazda-cx30 --export
uv run python -m bazaraki scrape --search mazda-cx30 --dry-run
```

Or a one-off probe with no saved search behind it:

```bash
uv run python -m bazaraki scrape --make mazda --model cx-30 --year-min 2018 --price-max 25000
```

To add a car, add it to `searches/cars.py` and give it a slug in
`bazaraki/cars.py`. Watch that slug: bazaraki prefixes the make on some models
(`toyota/toyota-rav4/`, not `toyota/rav4/`) — open the model's page and read the
last path segment.

Options for `scrape`:

| Flag | Default | Meaning |
|------|---------|---------|
| `--search NAME` | — | Saved search to crawl for; the whole configuration |
| `--make` / `--model` | — | Make/model slugs, for a one-off probe |
| `--price-min/max`, `--year-min/max`, `--mileage-min/max` | — | Range overrides for a probe |
| `--dry-run` | off | Print the filters and search URL, then exit without crawling |
| `--max-pages N` | 10 | How many listing pages to crawl (~60 ads/page). A run stopped by this limit is treated as truncated and skips delisting |
| `--no-details` | off | Skip per-ad detail pages — faster, but only the fields shown in the list view (price, mileage, gearbox, fuel, location, date) |
| `--concurrency N` | 1 | Concurrent requests (kept low to be polite) |
| `--export` | off | Also write the xlsx when the crawl finishes |

To be polite to the site (it rate-limits aggressively), the crawler caps itself
at **30 requests/minute** and defaults to a single concurrent request, so a run
is a steady trickle rather than a burst. Failed requests are retried with
backoff. Raise `--concurrency` only if you know the site tolerates it.

Re-export the current database at any time:

```bash
uv run python main.py export --out cars.xlsx
```

## Price history & lifecycle

To turn *asking* prices into a realistic *sale* price (see `PRICING_PLAN.md`), the
scraper records two extra signals on every run — run the daily scrape and they
accumulate automatically:

- **Price trajectory.** Each advert's price is logged to the `priceobservation`
  table on first sighting and thereafter only when it changes, so price cuts over
  time are preserved rather than overwritten.
- **Lifecycle.** Every scrape opens a `scraperun` (recording its filter scope).
  When a run **completes** (crawls all result pages, not stopped by `--max-pages`)
  any in-scope advert it no longer finds is marked `is_active = False` with a
  `delisted_at` timestamp — a proxy for "sold". A truncated run delists nothing,
  and delisting is bounded to the exact make/model + year/price/mileage scope the
  run covered, so scraping one model never touches another's rows.

`CarListing` gains `is_active`, `delisted_at`, a derived `days_on_market`
property, and a reserved `seller_type`; all appear in the xlsx export. `init_db`
adds the new columns to an existing database in place, so no manual step is
needed.

The scrape summary now reports adverts seen / delisted, e.g.
`Done. Saw 218 adverts, delisted 3. 232 listings total in bazaraki.db`.

## Pricing notebook

`pricing_analysis.ipynb` is a parameterized per-make/model view of the market
(see `PRICING_PLAN.md` Part C). It reads the DB and reuses `bazaraki/analysis.py` for all
estimation, drawing: price-vs-mileage and price-vs-year clouds with the fitted
regression curve, your query marked against the cloud, price-cut trajectories +
distribution, days-on-market vs. price percentile, and market state over time.

```bash
uv run jupyter lab pricing_analysis.ipynb
```

Set `MAKE`, `MODEL`, `QUERY_YEAR_RANGE`, `QUERY_MILEAGE_RANGE` in the first code
cell (`MODEL = None` pools all models of a make) and **Run All**. The notebook is
committed without outputs; the history-dependent charts fill in as daily runs
accumulate price and lifecycle data.

## Repository layout

This repo hosts two independent car-sourcing projects. They share a virtualenv
and `pyproject.toml`; everything else — code, tests, fixtures — is per-project:

| Package | What it does |
|---|---|
| `bazaraki/` | Scrapes bazaraki.com (Cyprus market) and models realistic sale prices. Documented here and in `PRICING_PLAN.md`. |
| `banzai24/` | Scrapes banzai24.com Japanese auction lots and reads their auction sheets with Claude vision. See `AUCTION_PLAN.md`. |

Each package owns its tests and fixtures (`bazaraki/tests/`, `banzai24/tests/`),
so a project is self-contained and could be lifted out of the repo whole. Both
databases live at the repo root (`bazaraki.db`, `auction.db`) so the auction
project can join against Cyprus prices for a comparable.

```bash
uv run pytest                    # both projects
uv run pytest bazaraki/tests     # one project
```

Run either project as a module — `python -m bazaraki`, `python -m banzai24`.
`main.py` remains as a shim for the `python main.py …` commands above.

### The auction morning

```bash
./daily.sh --headless                       # fetch + read + report + dashboard
uv run python -m banzai24 report --today    # re-render, free
uv run python -m dashboard open             # rebuild the pages and open them
```

`daily.sh` validates the searches, checks the session once, fetches each car —
each narrowing itself to its own closest upcoming auction day — reads this
morning's auction sheets, writes a `report.html` per run, crawls the same cars on
bazaraki, and rebuilds `runs/index.html` and `runs/competitors.html`.
`--no-cyprus` skips the crawl. Reading sheets costs money (~$0.015 each), but
the day narrowing keeps a two-car morning at roughly five sheets, and the
judgement the report exists to make is only on the sheet. See `AUCTION_PLAN.md`
for why `--today` matters.

### Searches

Each car is one file: `searches/<name>.toml`, read by **both** parsers and by the
dashboard. It is the **whole** declaration — nothing is inherited, so a filter
that is not in the file is not applied.

A search is made of **bands**. A `[[band]]` is a year, a mileage range, the
chassis codes it prices and the max bid for them; the site's own year and
mileage bounds — and the codes the fetch keeps — are the union of the bands and
are never written by hand. Two variants worth different money are two bands: the
RAV4's E-Four `AXAH54` and 2WD `AXAH52` share a year and a mileage range and are
¥445,000 apart.

```toml
car = "mazda-cx30"                     # how each parser spells it is its own business

[site]                                 # banzai24 filters these out for us,
transmission = "auto"                  # and the sheet re-judges them
grade = ["4", "4.5", "5"]

[api]                                  # we drop these ourselves, from list data.
exclude_colours = ["black", "blue"]    # Rejects never reach the report.

[sheet]                                # only the auction sheet can answer these
drivetrain = "2WD"
no_damage_codes = ["W", "X", "欠"]

[dashboard]
expected_profit_eur = 2000             # what this car has to earn

[competitors]                          # what a Cyprus advert must be to count
fuel_type = ["petrol", "hybrid petrol"]
engine_size_start = 1.8

[[band]]
year = 2023
body_model_code = ["DMEJ3P"]           # which variant this price is for
mileage_end = 50000
max_bid_jpy = { private = 1_805_000 }

  [band.competitors]                   # wider than the band on purpose
  year_start = 2019
  mileage_end = 120000
  exclude_phrases = ["hybrid x"]       # a trim *this band* is not selling
                                       # against; adds to [competitors]
```

```bash
uv run python -m searches list                         # what is declared
uv run python -m searches check                        # will they all load?
uv run python -m banzai24 fetch --search mazda-cx30
```

The report sorts every lot into one of three groups: **meets all requirements**
(the sheet was read and nothing on it disqualifies the car), **unconfirmed** (the
sheet is unread, or the field a requirement needed was blank), and **fails a
requirement**. Only the sheet can demote a lot — `[api]` rejects never appear at
all. `report` loads the `.toml` by the name the run recorded, so re-tuning a
requirement and re-running `report --today` re-judges the morning for free.

Damage codes are matched as **letters anywhere in the mark**, so `X` catches
`XX`, a `W` inside a compound code like `A3W2` is caught, and the severity digit
is ignored — `W3` is a worse repair mark than `W2`, so a rule naming the digit
would wave through the marks you most want to see.

### What to bid

Each card carries `max bid ¥X · area (house) −¥Y · bid reduced ¥Z`, where **`Z`
is the number to type into the bidding platform**. `X` comes from the band the
lot falls in — your all-in maximum for that year and mileage range, per 車歴,
declared in the search file. `Y` is that auction house's area cost from
`banzai24/inputs/auction_area_prices_2026.csv`, overridable with
`report --area-prices PATH`.

The bids are read **live**, not from the run, so re-rendering an old report
prices it at the bids you hold now. That is a deliberate exception to the rule
that a run's prices are stamped into it: see
`docs/adr/0004-bid-prices-are-read-live.md`.

The bid block prints on **every** card, in every group: a car you will not buy
is still one whose price you may want to know. Where `Z` cannot be computed the
card prints **why** instead of a number — "no band for TOYOTA RAV4 2023 ·
21,000 km", "unknown auction house: …". Nothing here can fail a report: a run
that named no search, or a mis-edited area price file, costs you the bid column
and says so at the top of the page. See `banzai24/bidding.py`.

Two assumptions are made and both are printed on the card. A sheet whose 車歴
box says neither rental nor private is priced as **private** — the dearer row,
and the only one that always resolves. And the **sheet outranks the API** for
mileage and year: the API rounds to the nearest 1,000 km while the bands match
to the kilometre, so a car whose sheet reads 50,415 km is priced from the
over-50,000 band. See `docs/adr/0001-sheet-outranks-api.md`.

### The dashboard

```bash
uv run python -m dashboard build      # write both pages
uv run python -m dashboard open       # …and open them in the parser's Chrome
```

Two static pages in `runs/`: `index.html` (the last ten runs, with a link) and
`competitors.html` — for each enabled search, **who is already selling this car
in Cyprus for less than you would have to charge**.

One section per band. A band's max bid gives a landed cost; landed cost plus
resale costs plus that car's `expected_profit_eur` gives a **cyprus sell price**;
a **competitor** is a live Cyprus advert, inside that band's declared competitor
bounds, asking less. The **Cyprus estimate** sits beside the sell price, because
if your required price is above it the band does not work at any profit and the
length of the list is a footnote.

An empty list is never rendered as good news. A search with no profit set, no
competitor bounds, or no completed bazaraki crawl covering its range gets a
section saying which of those it is — "0 competitors" and "nobody has looked" are
opposite findings.

Priced at **today's** money — a live FX fetch and today's cost book, with the
newest run's stamped pair as an offline fallback, and the rate's timestamp on the
page. This page is not a record of a past decision; it is the live question of
what to buy this morning.

`banzai24 report` no longer writes the index — it only opens it. Building these
pages needs both databases, a cost book and the network, and `report` promises
none of that.

## How it works

- `searches/` — the dependency root: one `.toml` per car, `SearchDefinition` and
  `Band`, validation, and `python -m searches list/check`. Imports no parser.
- `dashboard/` — `competitors.py` (the panel model), `index.py` (the runs list),
  templates, and `python -m dashboard build/open`. Imports both parsers; neither
  imports it.
- `bazaraki/cars.py` / `banzai24/cars.py` — how each site spells each car. A URL
  slug is a fact about a website, so it lives with the code that talks to it.
- `bazaraki/config.py` — `CarFilters` dataclass (the filter schema) + label→code
  maps and `build_search_url`, which turns filters into the make/model path plus
  query.
- `bazaraki/crawler.py` — Crawlee crawler: opens a `ScrapeRun`, resolves
  year/engine codes from the live page when needed, parses listing pages, follows
  pagination (preserving filters), enqueues detail pages (unless `--no-details`),
  and on finish delists in-scope adverts it didn't see.
- `bazaraki/payload.py` — pulls the JSON payload bazaraki's Next.js pages render
  from out of their inline RSC "flight" scripts; the site's own API data, which
  is steadier to read than the generated Tailwind markup.
- `bazaraki/parsers.py` — pure payload→dict parsing (`parse_cards`, `parse_detail`,
  pagination + option-code helpers), no network, so they're easy to test.
- `bazaraki/models.py` / `bazaraki/db.py` — `CarListing`, `PriceObservation` and
  `ScrapeRun` SQLModels; a SQLite upsert keyed on bazaraki's advert id (re-runs
  update rather than duplicate) that also logs price changes and refreshes
  lifecycle.
- `bazaraki/export.py` — writes the table to `.xlsx` (openpyxl).

Outputs (`bazaraki.db`, `*.xlsx`) and Crawlee's `storage/` dir are gitignored.

## Detail vs. list fields

The list view already provides: price, currency, title, url, image, photo count,
mileage, gearbox, fuel type, location, posted date. Visiting detail pages
(default) adds: exact posted timestamp, year, engine size, power, colour, body
type, doors, seats, drive, MOT date, availability and extras.