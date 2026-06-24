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

## Usage

Scrape the first few pages of the cars category and export to xlsx in one go:

```bash
uv run python main.py scrape --max-pages 3 --export
```

Options for `scrape`:

| Flag | Default | Meaning |
|------|---------|---------|
| `--max-pages N` | 3 | How many listing pages to crawl (~60 ads/page) |
| `--no-details` | off | Skip per-ad detail pages — faster, but only the fields shown in the list view (price, mileage, gearbox, fuel, location, date) |
| `--concurrency N` | 5 | Concurrent requests (keep modest to be polite) |
| `--url URL` | cars category | Scrape a different bazaraki category URL |
| `--export` | off | Also write the xlsx when the crawl finishes |

Re-export the current database at any time:

```bash
uv run python main.py export --out cars.xlsx
```

## How it works

- `crawler.py` — Crawlee crawler: parses listing pages, follows pagination, and
  (unless `--no-details`) enqueues each advert's detail page for enrichment.
- `parsers.py` — pure HTML→dict parsing (`parse_cards`, `parse_detail`), no
  network, so they're easy to test.
- `models.py` / `db.py` — `CarListing` SQLModel and a SQLite upsert keyed on
  bazaraki's advert id, so re-runs update rather than duplicate.
- `export.py` — writes the table to `.xlsx` (openpyxl).

Outputs (`bazaraki.db`, `*.xlsx`) and Crawlee's `storage/` dir are gitignored.

## Detail vs. list fields

The list view already provides: price, currency, title, url, image, photo count,
mileage, gearbox, fuel type, location, posted date. Visiting detail pages
(default) adds: exact posted timestamp, year, engine size, power, colour, body
type, doors, seats, drive, MOT date, availability and extras.