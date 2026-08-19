# Japanese auction lots → AI sheet extraction → assisted bidding

Implementation plan. Three systems, deliberately decoupled so each can be run,
re-run and debugged on its own:

```
 ┌── A. SCRAPE ────────────┐   ┌── B. ENRICH ─────────┐   ┌── C. REVIEW & BID ──┐
 │ banzai24 (login)        │   │ Claude vision reads  │   │ report.html, then   │
 │ JSON API → lot rows     │──▶│ each auction sheet   │──▶│ Claude in Chrome    │
 │ + sheet images          │   │ → structured fields  │   │ on the bidding site │
 └─────────────────────────┘   └──────────────────────┘   └─────────────────────┘
```

Decisions made (2026-08-08): same repo, new `banzai24/` package alongside `bazaraki/`; **SQLite is the
cross-run accumulator and every step also writes browsable files to disk**; the
review surface is a read-only per-run HTML report.

---

## Storage model

Two stores, answering two different questions.

**Run directories answer "what happened in this run?"** — immutable artifacts,
one directory per fetch, openable in Finder:

```
runs/2026-08-12-uss-tokyo/
  lots.json           raw API responses, exactly as received   ← step 1 visible
  sheets/             sheet images (800×800 JPEG)              ← step 1 visible
  lots.csv            flattened lot fields                     ← step 2 visible
  extractions.jsonl   one line per sheet, full model output    ← step 3 visible
  report.html         everything joined, for review            ← step 4
```

**`auction.db` answers "what do I know across all runs?"** — dedup by image
hash so a sheet is never paid for twice, the bid ledger, lot lifecycle across
repeat appearances, and joining to the Cyprus prices in `bazaraki.db`.

Files are the output of each step; the DB is the memory.

---

## Site facts (Phase 0 — verified 2026-08-08)

banzai24.com is an aggregator over Japanese auctions. **It is a Nuxt SPA backed
by a clean JSON API** — there is no HTML scraping to do at all.

### The endpoint

```
GET /api/catalog-service/lots            → the lot list (this is the one that matters)
GET /api/catalog-service/trade-days      → the auction-date tabs + per-day counts
```

Query params, taken verbatim from a live request:

```
company=24 & models[]=11528 & transmissions[]=AUTO
& yearStart=2023 & yearEnd=2023 & mileageEnd=55000 & engineCapacityStart=1.9
& gradeOrigin[]=4 & gradeOrigin[]=5 & gradeOrigin[]=4.5
& source=auctions & countryISO=JP
```

**The API takes a numeric `models[]` id (11528), not the `MAZDA/CX-30` slug the
UI URL shows.** So `AuctionFilters` needs a make/model → id resolution step —
structurally identical to the year/engine-code bootstrap the existing
`config.py` already does for bazaraki. `source=auctions` is upcoming lots;
`source=archive` is completed ones.

### Response shape

`{items: [...], pagination: {...}}`, one item per lot:

| Field | Example | Notes |
|---|---|---|
| `id` | `019fded9-2a2a-714c-…` | UUIDv7 → detail URL `/car/JP/{id}` |
| `lot.number` | `"47-1312-35159"` | **globally unique** — auction id + lot no |
| `lot.shortNumber` | `"35159"` | what's shown in the UI |
| `lot.auction` | `{id: 47, name: "HAA Kobe"}` | |
| `lot.tradeDate` / `tradeTime` | `2026-08-08` / `11:17` | |
| **`auctImage`** | image-service URL | **the auction sheet** |
| `grade` / `gradeOrigin` | `"4.5"` | overall auction grade |
| `status` | `{code: "SOLD", name: "Продан"}` | also `AWAITING_TRADE` etc. |
| `startPrice` / `endPrice` | `"1290000"` / `null` | strings; `currency: "JPY"` |
| `characteristics` | `{color, mileage, modification, bodyNumber, transmission, engineCapacity, steeringWheelSide, …}` | |
| `bodyModelCode` | `"5AA-DMEJ3P"` / `"DMEJ3P"` | **top level**, not under `characteristics`; mirrored in `car.shortCodeModel`. The type-designation prefix is present on some lots and absent on others *for the same model* — see the filter note below |
| `car` | `{mark, model, modelId, shortCodeModel}` | |
| `registrationYear` / `Month` | `2023` | |
| `images` / `imagesCount` | 6 photos + sheet | photo gallery, unused by us |
| `japanStatReportUrl`, `translationAL`, `tags` | | paid add-ons |

**`auctImage` is in the list response.** No per-lot detail page fetch is needed
to get the sheet — this collapses what I'd assumed would be N+1 requests into
one. Sheet images are served from `/api/image-service/<opaque token>`, are
**publicly fetchable with no auth or cookie** (verified with curl), and come
back as **800×800 JPEG, ~64 KB**.

> ⚠️ **Page 1 makes no API call at all.** Nuxt server-renders the first page of
> results into the HTML payload; `/lots` fires only when you change page or tab.
> Phase 0 half-saw this — every `/lots` capture came *after* a click — without
> drawing the conclusion, and Phase 1 then lost hours waiting on a request the
> site had no reason to make, misreporting each timeout as an expired session.
> So page 1 is read from the hydration state and pages 2+ are intercepted.
> Corollary: page 1 carries **no `pagination` block**, so totals must come from
> a later page (or the rendered pagination buttons).

### Auth and client requirements

The API rejects unauthenticated calls with `{"error":"missing token"}`. The
access token lives in the Pinia store in memory (`__NUXT__.pinia.user
.accessToken`), not in localStorage. **The design therefore does not touch tokens
at all** (see Phase 1); I attempted to read one during recon and it was correctly
blocked as credential extraction, which is both the right call and a pointer to
the better architecture.

Login is SMS 2FA, done by hand into a persisted Chrome profile, which does carry
auth across runs.

**`Accept-Language` must be Russian.** banzai24's backend answers `HTTP 500
Service Unavailable` to anything else. This cost a long detour in Phase 1 — it
presents exactly like bot-blocking, and it is not: `navigator.webdriver` was
already `false` and the UA was genuine Chrome. An A/B probe settled it (same
machine, same minute, `en` → 500, `ru-RU` → 200). `session.LOCALE` is load-bearing
and commented as such. Headless works fine once it is set.

### What the sheet actually contains

Verified against `banzai24/tests/fixtures/sheet_CAA-Chubu_2026-08-12_33152.jpg`:

> **The sheet is a digital render, not a photograph of paper.** Crisp vector-ish
> text at 800×800, fully legible. This removes the single biggest technical risk
> in the plan — there is no handwriting OCR problem here.

Fields present, with the Japanese labels to key the prompt on:

- `出品番号` lot no · `評価点` overall grade · `外装`/`内装` exterior/interior grade (A–E)
- `初度登録` first registration (**Japanese era**: `R5年1月` = Reiwa 5 = 2023-01)
- `走行` mileage · `車検` shaken expiry (blank = none) · `型式` model code
- `車台番号` **full chassis** — `DMEJ3P-103452`, where the API masks it as `DMEJ3P-10**55`
- `注意事項欄` warnings (`ﾋﾟSD欠品` = navi SD card missing)
- `検査員記入欄` inspector notes (`ハンドルすれ` = steering wheel scuffed)
- `セールスポイント` equipment highlights · `純正装備` factory equipment
- `車歴` vehicle history — `自家用` private use, `レンタカー`/`レンタ` ex-rental,
  `教習車` driving school, company/lease wording otherwise
- drivetrain, printed near the model code as `2WD` / `4WD` (sometimes `FF`/`FR`)
- damage map with codes placed on a car diagram (`A1`, `U1`)
- code legend printed on every sheet: `A`=scratch `U`=dent `B`=dent w/ scratch
  `P`=needs paint `W`=repair marks `S`=rust `C`=corrosion/hole
  `G`=windscreen chip `XX`=replaced `X`=needs replacing `欠`=missing part

Two findings that change downstream logic:

1. **Mileage differs between API and sheet** — API `15000`, sheet `15,415 km`.
   The API rounds. The mismatch check must tolerate rounding to the nearest
   1,000, or it will flag every single lot.
2. **The sheet reveals the full chassis number that the API masks.** That is
   real, concrete value from the extraction step, independent of everything else.

Also worth noting: banzai24 sells "order a translation of the auction sheet" as
a paid service. Phase 3 is a direct substitute for it.

### Still open

1. Bid amounts now have **half a home in this pipeline** (added 2026-08-17,
   reversing the earlier "no home, by design"). The report computes a
   `bid_reduced` per lot — `max_bid` from an operator-authored price table, minus
   the auction house's area cost — and prints it on the card. See
   `banzai24/bidding.py` and `BID_PRICING_QUESTIONS.md`.

   **Placing** the bid remains manual and unscoped. Nothing in this repo drives a
   browser to a bidding form, and wiring a computed number straight into an
   irreversible bid is a separate decision with a much larger blast radius — it
   gets its own grilling session before any of it is designed.

---

## Layout

The repo now hosts two sibling packages sharing one virtualenv and test suite
(split done 2026-08-08; `bazaraki/` was flat at the repo root before that):

```
bazaraki/                 # Cyprus market scraper + pricing analysis (existing)
banzai24/                 # this project
  __init__.py
  config.py       # AuctionFilters + query builder; model-slug → modelId resolution
  session.py      # Playwright browser + persisted login session
  fetch.py        # drive the SPA, intercept API responses, download sheets
  normalize.py    # API JSON → flat lot dicts (pure functions)
  models.py       # SQLModel tables
  db.py           # upsert / query layer
  sheets.py       # Claude vision extraction
  bidding.py      # max_bid − area cost → bid_reduced; pure, table-driven
  report.py       # run directory + auction.db → report.html
  templates/      # report.html.j2 — the only template; all CSS inline
  cli.py          # `uv run python -m banzai24 <command>`
  inputs/         # operator-authored tables, edited in a spreadsheet, not code
    bid_prices.csv              # your max bid per make/model/year/mileage/rental
    auction_area_prices_2026.csv  # each house's area cost, in JPY
    auction_aliases.csv         # "U Tokyo" = "USS TOKYO", and five more
  searches/       # one saved search per car — the run configuration
    daily.sh        # the morning: check, both cars, a report each
    mazda-cx30.sh
    toyota-rav4.sh
bazaraki.db  auction.db   # both at repo root, so the report can join across them
runs/<date>-<house>/
  tests/           # each project owns its tests
```

`normalize.py` replaces the planned `parsers.py`: with a JSON API there is no
HTML to parse, just field mapping — still pure functions tested against a saved
fixture, but far less brittle than selectors.

**No shared `common/` package for now.** The two projects both have a filters
dataclass, a SQLModel layer and a CLI, so the pull toward extracting a base is
real — but they differ where it counts (Crawlee + HTML vs Playwright + JSON,
different keys, different lifecycles). Duplicating ~30 lines of engine setup is
cheaper than committing to the wrong abstraction before `banzai24` exists. Revisit
once Phase 4 is running.

**Revisited at the end of Phase 4: still no `common/`.** The two packages did
finally meet, and the meeting says the opposite of what it looked like it would:
`report.py` imports `bazaraki.analysis` directly and calls
`filter_model`/`clean`/`comparables` on plain `CarRecord`s. That worked
first try *because* `analysis.py` was already free of database and network
dependencies — the useful sharing turned out to be one project importing
another's pure functions, which needs no third package. What is still duplicated
(engine setup, `_ensure_columns`, a filters dataclass) is the part that was
never worth extracting.

New dependencies: `playwright`, `anthropic`, `jinja2`.

---

## Phase 1 — Session and fetching

**The approach: let Playwright drive the real page and intercept the API
responses it makes.** Not HTML scraping, and not calling the API with a
hand-extracted token.

```python
async with browser_context() as page:
    captured = []
    page.on("response", lambda r:
        captured.append(r) if "/api/catalog-service/lots" in r.url else None)
    await page.goto(search_url(filters))
    ...
```

This is better than reimplementing the API client, not merely more convenient:

- **no auth reimplementation** — no bearer token, CSRF, refresh flow or expiry
  handling to get wrong, and nothing that looks like credential harvesting
- **survives their auth changes** — if banzai24 rotates the scheme, the page
  still works and so do we
- **still yields clean JSON**, so we keep every advantage of the API over HTML

`session.py`

- `login()` — headed browser; you enter credentials and the SMS code by hand,
  then `storage_state()` → `banzai24/.session.json` (gitignored).
- `check_session()` — runs **before** a crawl, not lazily mid-run. SMS 2FA means
  every expired session costs a phone round-trip: failing at request 1 costs one
  SMS, discovering it at request 40 costs an SMS *and* a half-finished run.
- An invalid session fails loudly rather than silently capturing logged-out
  responses.

`fetch.py`

- Writes captured responses verbatim to `runs/<run>/lots.json` before anything
  is normalized.
- **Narrows the run to the closest upcoming auction day** (`--all-days` opts
  out). A `source=auctions` search spans every scheduled day — 08-11, 08-12 and
  beyond — but only the nearest one is still worth reading sheets for. Two
  details make this non-trivial, both verified against saved runs:
  - **`min(tradeDate)` is the wrong rule.** Today's lots stay in the result set
    after they have traded, so on 2026-08-08 the minimum is 08-08, a day that is
    already over. A lot counts as upcoming only if its `status.code` is not
    terminal (`SOLD`/`SOLD_BY_NEGO`/`NOT_SOLD`/…) *and* its date is not past.
    The date check alone would keep this morning's sold lots; the status check
    alone would trust a stale status on an old lot.
  - **"Today" is Tokyo's date, not the machine's** (`fetch.japan_today`).
    `tradeDate` is a Japanese calendar date and Japan rolls over at 18:00 Cyprus
    time, so for six hours every evening the local date reads a day behind — a
    JST day that has already finished would compare as upcoming, leaving only
    the status check to reject it.
  - **Paging stops once a page offers no more of the nearest day.** Lots come
    back in day order only loosely — within a day they are grouped by auction
    house, and a page can hold a lot from a later day while the nearest one
    still runs on into the next page — so a later day alone does not end it.
    That is a deliberate stop, not truncation, so it does not raise the
    truncation warning.
  - No upcoming day at all (any `archive` search) is a no-op that keeps every
    lot, rather than a run that silently produces zero.

`lot_filters.py` — criteria applied to lots after they arrive, for what the
site's own search cannot express. Kept separate from `AuctionFilters`, which
becomes URL parameters: these never reach banzai24. They still cost the fetch,
but they keep sheet downloads and paid extraction off unwanted lots.

- **`body_model_code`** (`--body-model-code`, repeatable, OR-ed). Substring match
  on the chassis code. The field is inconsistent — banzai24 writes both
  `5AA-DMEJ3P` and a bare `DMEJ3P` for the same CX-30 — so **both sides are
  normalized to the part after the last hyphen**, dropping the type-designation
  prefix that encodes emissions class. Either spelling can therefore be asked
  for, and a prefix-only pattern (`5AA`) correctly matches nothing. Falls back
  to `car.shortCodeModel`, then to `characteristics.bodyNumber` — which is
  `DMEJ3P-10**32`, the same code split from the *other* end.
- **Filters run inside the chosen day, not before it.** The day is picked from
  everything on offer, then the filter runs within it; if nothing on that day
  matches, **the run is empty and later days are not searched**. The question
  being answered is "what should I look at for the next auction", and a match
  three weeks out is not a better answer than none. On the saved CX-30 run the
  nearest day is 08-11 whatever the filter says, so `--body-model-code DMEJ3P`
  returns nothing rather than reaching forward to the DMEJ3P on 08-12.
  Page-turning follows the same rule — a day emptied by the filter still stops
  paging. `--all-days` is the way to look further out.

**`--max-lots` replaces `--max-pages`** (default 20, one page's worth). It bounds
the lots the run **keeps** — after the day narrowing and the filters — because
those are the ones that cost a sheet download and a paid extraction. A page count
was only ever a proxy for that, and a bad one once filters exist: one page can
yield twenty keepers or none. Pages are turned as needed until the count is met
or the day is exhausted.

Since the count is of kept lots, it cannot bound the crawl on its own — a filter
matching nothing would page to the end looking for lots it will never find. The
day narrowing normally stops things long before that; `PAGE_SAFETY_LIMIT` (25
pages) is the floor under the `--all-days` case, and firing it is reported in the
summary rather than silently capping the run.
- Downloads each `auctImage` to `sheets/<lot_number>.jpg` with plain `httpx` —
  **no browser or auth needed**, since those URLs are public. Skips any lot whose
  hash already has an extraction row.
- Politeness: serial, delayed. With SMS in the loop, being rate-limited isn't
  just slow, it's a manual interruption.

Everything downstream reads from `lots.json` and `sheets/`, so re-normalizing,
re-extracting and re-reporting never touch the site or the session.

**Test:** `fetch --max-lots 20` produces a run directory with `lots.json` and N
sheet images; the saved JSON becomes the Phase 2 fixture.

---

## Phase 2 — Normalizing and storage

`normalize.py` — pure functions over the API JSON:

```python
normalize_lot(item: dict) -> dict        # API item → flat row
parse_era_date("R5年1月") -> (2023, 1)   # Japanese era → Gregorian
```

Output goes two places: upserted into `auction.db`, and written to
`runs/<run>/lots.csv` — the same rows in a file you can open in Numbers.

```python
class AuctionLot(SQLModel, table=True):
    lot_number: str = Field(primary_key=True)   # "47-1312-35159" — globally unique
    lot_short: str                              # "35159", for display
    banzai_id: str                              # UUIDv7 → /car/JP/{id}
    auction_id: int
    auction_name: str                           # "HAA Kobe"
    trade_date: date
    trade_time: str

    mark: str | None
    model: str | None
    model_id: int | None
    modification: str | None      # "20S PROACTIVE TOURING"
    body_model_code: str | None   # "DMEJ3P"
    body_number: str | None       # masked: "DMEJ3P-10**55"
    registration_year: int | None
    registration_month: int | None
    mileage_km: int | None        # API value — rounded
    engine_capacity: str | None
    fuel_type: str | None         # "petrol" | "hybrid" — null on most lots
    transmission: str | None
    colour: str | None
    steering: str | None

    grade_origin: str | None      # "4.5" — already on the list response
    status_code: str | None       # SOLD | AWAITING_TRADE | …
    start_price_jpy: int | None
    end_price_jpy: int | None
    currency: str | None
    source: str | None            # auctions | archive

    sheet_url: str | None         # auctImage
    sheet_path: str | None
    sheet_sha256: str | None
    sheet_status: str = "pending" # pending|extracted|failed|no_sheet

    first_seen_at: datetime
    last_seen_at: datetime
```

**`lot_number` replaces the composite key I'd planned.** banzai24 already issues
a globally unique `"47-1312-35159"` that encodes the auction — better than
synthesising `house:date:lot_no`, and it survives a lot being re-listed.

Note `grade_origin` comes free on the list response — so, as suspected, the
vision step is **not** there to recover the overall grade. It's there for the
sub-grades, damage map, shaken, warnings and full chassis, none of which the API
exposes.

```python
class SheetExtraction(SQLModel, table=True):
    lot_number: str = Field(primary_key=True, foreign_key="auctionlot.lot_number")
    extracted_at: datetime
    model_id: str                # "claude-opus-5" — provenance
    sheet_sha256: str
    raw_json: str                # full model output, verbatim

    interior_grade: str | None   # A–E
    exterior_grade: str | None   # A–E
    sheet_grade: str | None      # cross-check against grade_origin
    sheet_mileage_km: int | None # exact — 15415 vs API's 15000
    chassis_full: str | None     # unmasked
    damage_marks: str | None     # JSON: [{panel, code}]
    equipment: str | None        # JSON list
    warnings_ja: str | None      # 注意事項欄
    inspector_notes_ja: str | None
    inspector_notes_en: str | None
    drivetrain: str | None       # "2WD" | "4WD" | … as printed
    rental_car_note: str | None  # "レンタカー" if ex-rental, else None
    private_car_note: str | None # "車歴: 自家用" if private-use, else None
    confidence: float | None
```

The two `車歴` fields are mutually exclusive and both nullable: an ex-rental gets
`rental_car_note` set and `private_car_note` null; a private-use car the reverse;
a company/lease car or an unreadable field leaves **both** null. Encoding it as
two nullable notes rather than one enum keeps the printed wording verbatim —
`レンタカー` and `レンタ` both occur — and makes "the sheet didn't say" distinct
from "the sheet said neither", which a single `history` column would blur.

Two tables so a re-extraction rewrites only the AI-derived half. There is no
third: a `BidRecord` was specified here for a bidding runbook that was never
built, held zero rows for its whole life, and was dropped on 2026-08-17
(`banzai24/migrate_drop_bidrecord.py`). What the pipeline knows about money is
derived at report time and never stored — the price tables change far more often
than the lots do, and a stored `bid_reduced` goes stale silently, which is
exactly the failure that costs money.

Three decisions the schema above does not show, each forced by the real data:

- **`"0 ¥"` normalizes to `None`, not `0`.** banzai24 writes a zero end price
  for a lot that has not sold. Storing the zero would drag every average and
  minimum computed over the column toward nothing — and "did not sell" is
  already carried, correctly, by `status_code`.
- **Re-normalizing must not undo Phase 3.** Normalizing only knows an image is
  on disk, so it always proposes `sheet_status="pending"`. The upsert refuses
  that when the row is already `extracted` **and the hash is unchanged** —
  otherwise every re-normalize would silently re-queue the whole database at
  $0.02 a sheet. A *changed* hash does go back in the queue, which is right: it
  is a different photograph, and the old extraction describes something else.
- **A run normalizes the lots it kept**, not every lot it saw; `--all` widens
  it. The kept lots are the run's subject and the only ones with sheets. The
  later days are still saved in `lots.json`, so widening later costs nothing.

`sheet_path` is stored relative to the project root, since Phase 4 expects a run
directory to survive being copied elsewhere.

**`fuel_type` is left null rather than inferred.** It is blank on 72 of the 112
saved lots, and `characteristics.engine` only looks like a second source: it
names the fuel (`"2.5 л / Гибрид"`) in exactly the cases `fuelType` already
does, so falling back to it would recover nothing. Engine size is not evidence
of fuel. Where it *is* recorded it earns its column — RAV4 lot `65-1953-2391`
is a hybrid whose `modification` string ("Z 4WD") never says so.

Adding it also needed `db._ensure_columns`: `create_all` creates missing tables
but never alters an existing one, so a model gaining a field breaks every read
of a database created before it. That diffs models against tables and adds what
is missing, so the next added column costs nothing.

**Test:** normalize the saved `lots.json` fixture → upsert → re-upsert is
idempotent.

---

## Phase 3 — Reading auction sheets with Claude

`sheets.py`. The model returns a validated object, not prose — a Pydantic schema
via `client.messages.parse()`, enforced server-side.

```python
resp = client.messages.parse(
    model="claude-opus-5",
    max_tokens=4000,
    messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64",
                                     "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": PROMPT},
    ]}],
    output_format=SheetData,
)
```

The prompt keys on the Japanese field labels catalogued above, includes the
damage-code legend (which is printed on every sheet, so it can be stated as
ground truth rather than guessed), and instructs `null` for anything illegible
rather than a guess.

Operational details:

- **Send at native 800×800.** That's the maximum banzai24 serves — well under
  Opus 5's 2576px ceiling, so there's no downscaling decision to make, and the
  digital-render quality means it's sufficient.
- **Cost: ≈ $0.015/sheet warm, measured — better than the $0.02 estimated.**
  Real usage from a live run: **860 input tokens** (the image, as predicted —
  800×800 is ~850, not the ~4.8k a full-resolution photo would cost), **3,432
  cached prompt tokens**, **345 output tokens**. Thinking is on by default on
  Claude Opus 5 (unlike Opus 4.8, where omitting the parameter meant no
  thinking), but at `effort: medium` on a task this scoped it barely spends —
  345 output tokens total. The first sheet of a run pays the cache write
  (≈ $0.035); every one after it reads the prompt at a tenth of input price.
  A 200-lot backlog is ≈ $3.
- **The prompt is cached, the image is not.** The instructions are identical for
  every sheet and comfortably over Claude Opus 5's 512-token cache minimum, so
  they go in `system` behind a cache breakpoint and the image goes after them.
  Caching is a prefix match, so that order is what makes it work at all — every
  sheet after the first reads the prompt at ~10% of input price.
- **`max_tokens` bounds thinking and answer together.** 8000, almost all of it
  headroom: a truncated extraction is worse than a slow one, and hitting the cap
  raises rather than silently returning half a sheet.
- **Idempotency by hash**; `--force` re-runs.
- **Cross-checks — four, each a cheap signal on both the pipeline and the lot:**
  - `sheet_grade` vs `grade_origin` — should match exactly; a mismatch means a
    misread or a relisted car.
  - `sheet_mileage_km` vs `mileage_km` **rounded to the nearest 1,000**. The API
    rounds and the sheet does not (15,415 vs 15,000); a naive comparison flags
    every lot and the signal is worth nothing.
  - **`chassis_full` vs the masked `body_number`, position by position.** The API
    publishes `DMEJ3P-10**52` and the sheet prints `DMEJ3P-103452`, so every
    unmasked character must agree. This is the strongest of the four: the chassis
    is the field with the most value and the least redundancy, and nothing else
    would catch a hallucinated one.
  - **`first_registration_raw` vs `registration_year`/`_month`**, via
    `parse_era_date`. The sheet prints `R5年1月` and the API says 2023/1 — which
    is what that function was written for. The month is only compared when the
    API has one, since it is null on most lots.

Results are appended to `runs/<run>/extractions.jsonl` as they land, as well as
written to the DB — written per sheet rather than batched at the end, because
these cost real money and a crash twenty sheets in should not discard twenty
sheets' worth of paid extraction.

**Two columns the table above was missing**, both for fields the prose already
promised the vision step would recover:

- **`shaken_expiry_raw`.** 車検 was listed as a reason the extraction exists but
  had no column. A blank box is the valuable case — no shaken means the buyer
  pays to put the car back on the road — so it is stored raw and nullable, and a
  null means "the sheet said nothing", never "the model gave up".
- **`first_registration_raw` + `_year` + `_month`.** The raw form is kept
  alongside the parsed one for the same reason the 車歴 notes are: it is what the
  sheet actually says. The parsed pair is what the fourth cross-check compares.

**Sheet layouts vary by auction house, and the prompt survives it.** The fixture
(CAA Chubu) has separate `外装`/`内装` boxes and a `検査員記入欄`; lot 35159
(GOプライムコーナー) has no exterior grade at all, labels its interior box
`内装補助評価`, and calls the notes `検査員報告`. The extraction handled both and
returned `exterior_grade: None` for the sheet that genuinely has no such box —
which is the "null over guesses" rule doing exactly its job. It also returned a
damage code not in the legend (`トビA`, a stone chip) verbatim rather than forcing
it into a known code.

One open semantic question this surfaced: that sheet labels the box
`車歴(自家用以外は記入)` — "fill in only if **not** private use" — and leaves it
blank. Blank therefore *means* private use, but the extraction returns null
because nothing is printed. Null is defensible ("the sheet didn't say") and is
what the current prompt asks for, but it is a third case the two-nullable-notes
design did not anticipate. Left as-is deliberately; changing it is a decision
about what the column means, not a bug fix.

**Batch API is not implemented yet.** The sync path is complete and idempotent,
so a backlog runs today at full price; `client.messages.batches` with
`custom_id = lot_number` halves that and is the obvious next increment. It needs
the schema passed as `output_config.format` rather than through
`messages.parse()`, which is why it is a separate piece of work rather than a
flag.

**Test:** golden-file test over the saved fixture sheet. Known-good values from
lot 33152: grade `5`, exterior `A`, interior `B`, mileage `15415`, chassis
`DMEJ3P-103452`, damage marks `A1` and `U1`, note `ハンドルすれ`, warning
`ﾋﾟSD欠品`, no shaken. Assert on those, not on prose.

---

## Phase 4 — The review report

`report.py` → `runs/<run>/report.html`. **Read-only.** Self-contained, sheet
images inlined as data URIs, opens by double-click, works after the directory is
copied anywhere.

This is what a spreadsheet cannot do: the sheet scan next to the extracted
fields. Grade 4.5 with an `A1` mark means nothing without the image.

```
┌───────────────────────────────────────────────────────────┐
│ Lot 33152 · CAA Chubu · 2026-08-12 12:00 · awaiting trade │
│ ┌────────────┐  MAZDA CX-30 20S Proactive Touring · 2023  │
│ │            │  Grade 5 · exterior A · interior B         │
│ │   sheet    │  15,415 km  ✓ (API said 15,000)            │
│ │   800×800  │  A1 right rear · U1 roof                   │
│ │            │  ⚠ navi SD card missing                   │
│ └────────────┘  steering wheel scuffed                    │
│                 chassis DMEJ3P-103452 (API masked)        │
│                 Cyprus comparable: €18,400 (n=7)          │
│                 start ¥390,000                            │
│                 max bid ¥1,855,000                        │
│                 area (U Tokyo) −¥12,000                   │
│                 bid reduced ¥1,843,000                    │
└───────────────────────────────────────────────────────────┘
```

Sorting and flagging carry the load: low confidence and grade mismatches surface
at the top with colour flags, so the lots needing your eyes aren't buried behind
clean ones. `bid_reduced` deliberately flags nothing and changes no ordering — it
is a number to read off the card you are already looking at, and a page that
re-sorted itself every time the price table was re-tuned would stop being stable.

Jinja2 from the run directory, plus a DB query for the extraction, the Cyprus
comparable, and two operator-authored CSVs for the bid arithmetic. `report` is
its own command — regenerating is free, so a template tweak never means
re-fetching or re-extracting.

    uv run python -m banzai24 report                    # the most recent run
    uv run python -m banzai24 report --open
    uv run python -m banzai24 report --jpy-per-eur 172  # also price in euro

**Which lots, and from where.** The run directory decides *which* lots — it is
the record of what the fetch was about — and the database decides *what is
known* about them, since extractions and bids accumulate across runs and a lot
seen twice should show both. A lot in the run but not yet in the database is
rendered from `lots.json` anyway, with a banner naming `normalize` as the fix:
the reason a card is thin belongs on the page, not in the operator's memory.

**Four flags, ranked, and the ranking is the sort key** — one ordering rather
than a severity scale plus a separate sort rule that can drift out of step:

| | Flag | Why it outranks the next |
|---|---|---|
| 50 | cross-check mismatch | the sheet and the API disagree about the same car |
| 40 | bid placed | money is already committed here |
| 30 | confidence < 0.9 | the model is telling you to look at the scan yourself |
| 10 | sheet not read | unfinished work, not a finding |

**No shaken is not among them**, though it was at first. It is a real cost, but
a blank 車検 box is the ordinary state of an export lot — the badge fired on most
of the page, which is the same as flagging nothing. The fact stays in the card,
printed against the 車検 field where the money is worked out.

**"Not read" sorts above a clean lot but is not counted as flagged.** Both
halves matter. It sorts high because an unextracted sheet is outstanding work
and a clean extracted lot is the one needing you least. It is excluded from the
header's flagged count because it says nothing about the *car* — counted, a
freshly fetched run would report every lot as flagged and the number would stop
meaning anything. The header says `1 flagged · 4 sheets not read yet` instead.

Four things the build turned up, three of them only visible on the rendered page:

- **`autoescape=True`, not `select_autoescape`.** That helper keys on the file
  extension, sees `.j2` rather than `.html`, and silently leaves escaping *off*.
  Silently is the problem: the page renders and looks right, and half of what
  goes into it is model-transcribed sheet text where one `<` would eat the rest
  of the card. A test asserting a `<b>` in an inspector's note comes out escaped
  is what caught it.
- **`height: auto` on the sheet image is load-bearing.** The `<img>` carries
  `width`/`height` attributes so the page reserves the right box before a
  ~180 KB inline data URI decodes — but with only `width` set in CSS, the 800px
  *height attribute* wins and every 800×800 sheet renders as a stretched
  ribbon. Visibly wrong, and invisible to every assertion worth writing.
- **Zooming a sheet has to reflow the card, not just the image.** At 800px the
  fields column is squeezed into a two-word-wide strip beside it; the zoomed
  state drops the fields onto their own full-width row. The zoom itself is a
  checkbox and a `:checked ~` selector — no JavaScript, so the file stays inert
  wherever it is opened.
- **`sheets.cross_check` now takes a stored `SheetExtraction` as well as a fresh
  `SheetData`.** The four compared fields are spelled identically on both, so
  the report re-runs the same tested comparisons over the database months later
  rather than reconstructing the model's output from `raw_json`. The marks are
  printed *against the value they qualify* — `22,895 km ✓ (API said 23,000 km)`
  — because a cross-check collected into a separate block is a fact about the
  pipeline, while one printed next to the number is a fact about the car.

**The Cyprus join is a median asking price, and says so.** It goes through
`bazaraki.analysis.filter_model`, which normalises case and punctuation — that
is exactly the gap between banzai24's `MAZDA`/`CX-30` and bazaraki's
`Mazda`/`CX-30`, so the two databases join on nothing but strings and survive
it. Listings load once per report and the per-model subsets are cached, so a
one-model run is one query however many lots it holds. `comparables()` rather
than `estimate_sale_price()`: the report shows the median of real nearby
adverts with its `n` and band (`€21,900 · n=126 · high · ±1y ±15k km`), and a
fitted sale estimate would look more precise than the input deserves. **A
missing or unreadable `bazaraki.db` is reported, not raised** — the Cyprus
number is context, and a report without it is still the sheet next to the
fields, which is the point of the page.

**No default yen/euro rate.** `--jpy-per-eur` is opt-in and prints the rate it
used next to the figure. A hard-coded rate goes stale silently and a stale one
is worse than none, on the one number the whole exercise turns on.

**Test:** render against fixture data; assert flagged rows sort first, that
every `src` is a `data:` URI (hyperlinks to banzai24 lot pages are fine — the
browser never fetches them), and that the sheet's own numbers reach the page.

---

## Build order

| # | Phase | Depends on | Done when |
|---|-------|-----------|-----------|
| 0 | Recon | — | ✅ **done** — API mapped, sheet fixture saved, legibility proven |
| 1 | Session + fetch | 0 | ✅ **done** — 5 lots + 5 sheets; pagination verified over 2/17 pages |
| 2 | Normalize + store | 1 | fixture round-trips; `lots.csv` + `auction.db` populated |
| 3 | Sheet extraction | 2 | golden-file test green on lot 33152's known values |
| 4 | Report | 3 | ✅ **done** — `report.html` opens standalone; flagged rows first |

Phase 3 can be built immediately from the saved fixture sheet, in parallel with
Phases 1–2 — it needs no network access at all.

There is no Phase 5. A "bidding runbook" phase was specified here and removed on
2026-08-17 without ever being built: the report now tells you what to bid, and
how that number reaches a bidding form is deliberately unscoped (see "Still
open" #1).

---

## Phase 1 result (2026-08-08)

`uv run python -m banzai24 fetch --max-pages 1` → 5 lots, 5 sheets, in
`runs/2026-08-08_223155_MAZDA-CX-30/`. Verified:

- every field matches what the browser showed during recon (houses, dates,
  grades, mileages, statuses);
- the downloaded sheet for lot `55-1850-33152` is **byte-identical** to the
  Phase 0 fixture pulled by hand — the download path is proven end to end;
- pagination over `--max-pages 2` returned **40 unique lots, zero duplicates**,
  and correctly reported 338 lots across 17 pages.

Lessons worth keeping, since both cost real time:

1. **Diagnose before theorising.** "Bot detection" and "expired session" were
   both wrong; one A/B probe and one page-body dump found the actual causes
   (Russian locale, and page 1 making no API call). The dump now ships in the
   error path — a timeout prints the URL, title and visible text.
2. **A missing request looks exactly like a failed one.** Three different
   symptoms all surfaced as `TimeoutError waiting for response`. Distinct
   exception types (`SessionExpired`, `ServiceUnavailable`) plus the page dump
   are what make them tellable apart.

## Phase 4 result (2026-08-09)

`report runs/2026-08-08_223155_MAZDA-CX-30 --jpy-per-eur 172` → a 910 KB
self-contained `report.html`: 5 lots, 1 flagged, 1 with sheet data, 4 sheets not
read yet. Every `src` in it is a `data:` URI; the only external URLs are
hyperlinks to the banzai24 lot pages, which are never fetched.

**The page is also the first end-to-end check of the extraction, because you can
read the sheet next to what was read off it.** Zooming lot 35159 confirmed by
eye: `22,895 km`, `DMEJ3P-109555`, grade `4.5`, interior `B`, no exterior box on
that layout, a blank 車検, and `トビA` sitting on the bonnet in the damage
diagram — all exactly as the extraction stored them. Two Phase 1–3 decisions
show their value here rather than in any test: the mileage cross-check reads
`22,895 km ✓ (API said 23,000 km)` instead of flagging a lot that is fine, and
the chassis line prints the unmasked number beside the API's `DMEJ3P-10**55`.

The Cyprus join returned `€21,900 · n=126 · high · ±1y ±15k km` against a
`¥1,290,000` start — 126 comparable CX-30 adverts is a thick enough sample to
be worth putting on the page, which was not obvious before it ran.

## The daily run (2026-08-09)

The actual use case: **bid on a RAV4 and a CX-30 most days, so every morning
needs the analysed lots for each car's closest upcoming auction.**
`banzai24/searches/daily.sh` is that morning, and it is a script rather than two
commands typed in a row for one reason:

```bash
./banzai24/searches/daily.sh              # 20 lots per car + sheets
./banzai24/searches/daily.sh --headless   # no browser window
./banzai24/searches/daily.sh --dry-run    # both search URLs, fetch nothing
```

**Session first, once, before either fetch.** Login is SMS 2FA, so a dead
session costs a phone round-trip: learning that before the first car costs one
SMS, learning it between the two costs the same SMS *plus* a half-finished
morning. The script owns only the running order — each car's filters stay in its
own saved search, and extra flags pass through to both.

**It fetches and reports; it does not extract.** Reading sheets is the only step
that costs money, and it is worth deciding on *after* seeing what the morning
turned up. The reports built by `daily.sh` already carry grades, prices, Cyprus
comparables and a "sheet pending" badge on every lot — enough to choose what to
pay to read. The script ends by printing the two commands that finish the job.

Three things this exposed, none of them visible from one car:

- **`check` was broken by the very trap Phase 1 documented.** It waited for a
  `/lots` response that page 1 never makes, so it timed out against a perfectly
  healthy session and reported it expired. The fetch path was fixed for this in
  Phase 1; `check_session` was not, and nothing caught it because nobody runs
  `check` on its own — until a script made it step one. It now reads page 1 from
  the hydration state exactly as `fetch_lots` does. **A check whose failure mode
  is "your session is fine, and I say it is dead" is worse than no check**: it
  sends you to re-authenticate by SMS for nothing.
  It also stopped reporting a total it could not know — the SSR payload carries
  no `pagination` block, so it returns `SessionCheck(lots_on_first_page, pages)`
  and says "20 lots on page 1 of 17" rather than multiplying to a fake total.
- **Extraction results were being written to the wrong run directory.** The
  queue is global — every pending sheet in the database — but every result was
  appended to whichever single run directory the caller passed. One car a day
  hides this completely; two runs in one morning means one run's
  `extractions.jsonl` describes lots it never saw and the other is missing its
  own. The database was right either way, which is exactly why it could go
  unnoticed: the damage was only to the run directory, the artifact whose whole
  job is to be an honest record of one run. Results now route per lot via
  `sheets.run_dir_of`, read off `sheet_path`.
- **`extract` with no arguments pays to read history.** The queue is every
  pending sheet ever downloaded, so on day three it includes lots that traded
  last week. `--today` scopes it to sheets from today's runs, and a run
  directory scopes it to one car. On the first real morning that was the
  difference between $0.30 and $0.21, and between reading stale lots and
  current ones.

`--today` is on both `extract` and `report` for the same reason: **one morning
is now several runs**, so `latest_run()` answers the wrong question — it would
report on the second car and silently skip the first.

**First real morning:** 1 CX-30 and 6 RAV4 lots, both on 2026-08-11, 7 sheets
downloaded, three reports written, nothing spent.

## Saved searches

One shell script per car under `banzai24/searches/`, each holding that car's
whole filter set and passing extra flags through:

```bash
./banzai24/searches/mazda-cx30.sh                  # 1 page + sheets
./banzai24/searches/toyota-rav4.sh --max-pages 5   # more pages
./banzai24/searches/toyota-rav4.sh --dry-run       # print the URL, fetch nothing
```

Two things worth knowing:

- **The slug is `RAV4`, not `RAV-4`** — verified against the site; the hyphenated
  form does not resolve.
- **Every script passes `--no-defaults`.** Without it a saved search inherits
  `config.DEFAULT_FILTERS` for anything it does not set — the RAV4 search would
  have silently picked up the CX-30's `engine_capacity_start=1.9` and dropped
  RAV4 trims. The RAV4 config sets no engine filter on purpose, since its lineup
  spans 2.0/2.5 petrol and hybrid; a live run returned 17 lots including hybrids.

## Phase 0 artifacts

- `banzai24/tests/fixtures/sheet_CAA-Chubu_2026-08-12_33152.jpg` — 800×800 sheet,
  known-good values recorded in Phase 3 above.
- Example lot for end-to-end testing: banzai24 `/car/JP/019fded9-2a2a-714c-96e3-fcff2823fcb7`,
  lot `33152`, CAA Chubu, 2026-08-12.
- The `lots.json` fixture is captured by Phase 1's first run rather than saved
  by hand — the fetch code produces it as a normal artifact.