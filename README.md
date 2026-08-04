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

What to scrape is described by the `CarFilters` dataclass in `config.py` — the
single place listing every available site filter. Edit `DEFAULT_FILTERS` there:

```python
DEFAULT_FILTERS = CarFilters(
    make="mazda",       # URL slug:  Motors > Cars > Mazda > CX-30
    model="cx-30",      #            => .../cars-trucks-and-vans/mazda/cx-30/
    price_max=25000,    # EUR
    year_min=2018,      # calendar year
)
```

Available fields: `make`, `model`, `price_min/max`, `year_min/max`,
`mileage_min/max`, `engine_size_min/max` (e.g. `"1,6L"`), `gearbox`
(`"Automatic"`/`"Manual"`), `fuel_type` (`"Petrol"`, `"Diesel"`, …), `drive`,
`doors`, plus multi-select `body_type` / `colour` / `seats` / `extras`, and
`q` (free-text). `make`/`model` are the URL slugs — to find a model's slug,
open its page on bazaraki and read the last path segment (e.g. `mazda-6`, `323`).

Filter values are human-friendly: ranges use real values (`price_max=25000`),
enumerations use labels (`fuel_type="Petrol"`). Year/engine-size codes are
resolved automatically from the live page before the crawl.

## Usage

Scrape using `DEFAULT_FILTERS` and export to xlsx in one go:

```bash
uv run python main.py scrape --max-pages 3 --export
```

Quick overrides for the common filters (no need to edit `config.py`):

```bash
uv run python main.py scrape --make mazda --model cx-30 --year-min 2018 --price-max 25000
```

Options for `scrape`:

| Flag | Default | Meaning |
|------|---------|---------|
| `--make` / `--model` | from config | Make/model slugs (override `DEFAULT_FILTERS`) |
| `--price-min/max`, `--year-min/max` | from config | Range overrides |
| `--max-pages N` | 3 | How many listing pages to crawl (~60 ads/page) |
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
(see `PRICING_PLAN.md` Part C). It reads the DB and reuses `analysis.py` for all
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

## How it works

- `config.py` — `CarFilters` dataclass (the filter schema) + label→code maps and
  `build_search_url`, which turns filters into the make/model path plus query.
- `crawler.py` — Crawlee crawler: opens a `ScrapeRun`, resolves year/engine codes
  from the live page when needed, parses listing pages, follows pagination
  (preserving filters), enqueues detail pages (unless `--no-details`), and on
  finish delists in-scope adverts it didn't see.
- `parsers.py` — pure HTML→dict parsing (`parse_cards`, `parse_detail`,
  pagination + option-code helpers), no network, so they're easy to test.
- `models.py` / `db.py` — `CarListing`, `PriceObservation` and `ScrapeRun`
  SQLModels; a SQLite upsert keyed on bazaraki's advert id (re-runs update rather
  than duplicate) that also logs price changes and refreshes lifecycle.
- `export.py` — writes the table to `.xlsx` (openpyxl).

Outputs (`bazaraki.db`, `*.xlsx`) and Crawlee's `storage/` dir are gitignored.

## Detail vs. list fields

The list view already provides: price, currency, title, url, image, photo count,
mileage, gearbox, fuel type, location, posted date. Visiting detail pages
(default) adds: exact posted timestamp, year, engine size, power, colour, body
type, doors, seats, drive, MOT date, availability and extras.