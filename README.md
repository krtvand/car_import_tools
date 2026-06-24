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
| `--concurrency N` | 5 | Concurrent requests (keep modest to be polite) |
| `--export` | off | Also write the xlsx when the crawl finishes |

Re-export the current database at any time:

```bash
uv run python main.py export --out cars.xlsx
```

## How it works

- `config.py` — `CarFilters` dataclass (the filter schema) + label→code maps and
  `build_search_url`, which turns filters into the make/model path plus query.
- `crawler.py` — Crawlee crawler: resolves year/engine codes from the live page
  when needed, parses listing pages, follows pagination (preserving filters), and
  (unless `--no-details`) enqueues each advert's detail page for enrichment.
- `parsers.py` — pure HTML→dict parsing (`parse_cards`, `parse_detail`,
  pagination + option-code helpers), no network, so they're easy to test.
- `models.py` / `db.py` — `CarListing` SQLModel and a SQLite upsert keyed on
  bazaraki's advert id, so re-runs update rather than duplicate.
- `export.py` — writes the table to `.xlsx` (openpyxl).

Outputs (`bazaraki.db`, `*.xlsx`) and Crawlee's `storage/` dir are gitignored.

## Detail vs. list fields

The list view already provides: price, currency, title, url, image, photo count,
mileage, gearbox, fuel type, location, posted date. Visiting detail pages
(default) adds: exact posted timestamp, year, engine size, power, colour, body
type, doors, seats, drive, MOT date, availability and extras.