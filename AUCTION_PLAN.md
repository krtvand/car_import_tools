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

1. **Bidding site URL** — blocks Phase 5 only; Phases 1–4 proceed without it.
2. Bid amounts have **no home in this pipeline**, by design. How a max bid
   reaches the browser session is undecided — simplest answer is that you name
   it per lot at bid time, having read the report. Settle before Phase 5.

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
  report.py       # run directory + auction.db → report.html
  cli.py          # `uv run python -m banzai24 <command>`
  searches/       # one saved search per car — the run configuration
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
  - **Paging stops as soon as a later day appears.** Lots come back in trade-date
    order, so a second day showing up means the nearest one is complete. That is
    a deliberate stop, not truncation, so it does not raise the `--max-pages`
    warning.
  - No upcoming day at all (any `archive` search) is a no-op that keeps every
    lot, rather than a run that silently produces zero.
- Downloads each `auctImage` to `sheets/<lot_number>.jpg` with plain `httpx` —
  **no browser or auth needed**, since those URLs are public. Skips any lot whose
  hash already has an extraction row.
- Politeness: serial, delayed. With SMS in the loop, being rate-limited isn't
  just slow, it's a manual interruption.

Everything downstream reads from `lots.json` and `sheets/`, so re-normalizing,
re-extracting and re-reporting never touch the site or the session.

**Test:** `fetch --max-pages 1` produces a run directory with `lots.json` and N
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
    shaken_expiry: str | None
    damage_marks: str | None     # JSON: [{panel, code, note}]
    equipment: str | None        # JSON list
    warnings_ja: str | None      # 注意事項欄
    inspector_notes_ja: str | None
    inspector_notes_en: str | None
    confidence: float | None


class BidRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    lot_number: str = Field(index=True, foreign_key="auctionlot.lot_number")
    submitted_at: datetime
    amount_jpy: int
    outcome: str | None          # won|lost|error|unknown
    note: str | None
```

Two tables so a re-extraction rewrites only the AI-derived half. `BidRecord` is
append-only, written *after* a bid by Phase 5 — it exists for auditability and
double-bid prevention, not to plan bids.

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
- **Cost is lower than first estimated.** 800×800 ≈ 850 image tokens, not the
  ~4.8k a full-resolution photo would cost. With prompt and output that's
  **≈ $0.02/sheet** on `claude-opus-5`, or **≈ $0.01 batched**. A 200-lot
  backlog is a couple of dollars — cheap enough that re-extracting everything
  after a prompt improvement is not a budget decision.
- **Batch API** (`client.messages.batches`, `custom_id = lot_number`) for
  backlogs at 50% off; sync path for single lots.
- **Idempotency by hash**; `--force` re-runs.
- **Cross-checks**, each a cheap signal on both the pipeline and the lot:
  `sheet_grade` vs `grade_origin` (should match exactly — a mismatch means a
  misread or a relisted car), and `sheet_mileage_km` vs `mileage_km`
  **rounded to the nearest 1,000** (the API rounds; a naive comparison flags
  everything).

Results are appended to `runs/<run>/extractions.jsonl` as they land, as well as
written to the DB.

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
│ │            │  ⚠ no shaken · ⚠ navi SD card missing      │
│ └────────────┘  steering wheel scuffed                    │
│                 chassis DMEJ3P-103452 (API masked)        │
│                 Cyprus comparable: €18,400 (n=7)          │
│                 start ¥390,000 · not yet bid              │
└───────────────────────────────────────────────────────────┘
```

Sorting and flagging carry the load: low confidence, grade mismatches, missing
shaken and lots with a `BidRecord` all surface at the top with colour flags, so
the lots needing your eyes aren't buried behind clean ones.

Jinja2 from the run directory plus a DB query for bid history and the Cyprus
comparable. `report` is its own command — regenerating is free, so a template
tweak never means re-fetching or re-extracting.

**Test:** render against fixture data; assert flagged rows sort first and no
external image references remain.

---

## Phase 5 — Bidding runbook (Claude in Chrome)

A written procedure in `banzai24/BIDDING.md`, plus guardrails. Input is the run
directory you just reviewed and the amount you name per lot.

1. You pick a lot from the report and state a max bid.
2. The session navigates and confirms the on-page lot number **and** car match
   the run data before touching a field. A mismatch aborts the lot.
3. It enters the amount and **stops with the filled form visible for your
   confirmation.** It never auto-submits.
4. After you confirm, the outcome is appended via `record_bid()`.
5. `--dry-run` does everything except the final click.

This phase spends real money and bids are not reversible. The value of the split
is that Phases 1–4 can be developed, broken and re-run freely, while the only
step that commits funds stays human-confirmed, one lot at a time.

---

## Build order

| # | Phase | Depends on | Done when |
|---|-------|-----------|-----------|
| 0 | Recon | — | ✅ **done** — API mapped, sheet fixture saved, legibility proven |
| 1 | Session + fetch | 0 | ✅ **done** — 5 lots + 5 sheets; pagination verified over 2/17 pages |
| 2 | Normalize + store | 1 | fixture round-trips; `lots.csv` + `auction.db` populated |
| 3 | Sheet extraction | 2 | golden-file test green on lot 33152's known values |
| 4 | Report | 3 | `report.html` opens standalone; flagged rows first |
| 5 | Bidding runbook | 4 | dry-run walkthrough on one lot |

Phase 3 can be built immediately from the saved fixture sheet, in parallel with
Phases 1–2 — it needs no network access at all.

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