# Market Price Analysis — Plan

Goal: given a **make, model, year range, and mileage range**, estimate a **realistic price
the car can be sold for in Cyprus** — to support import (bid) decisions for cars from Japan.

## What we have vs. what the price question needs

- **218 rows, all Mazda**, clean on the fields that matter (`price`, `year`, `mileage_km`,
  `make`, `model` — zero nulls). `power_hp` is ~73% null, so don't lean on it.
- **The critical gap:** the DB only keeps *current* price + `first_seen_at` / `last_seen_at`.
  Daily updates overwrite, so we currently throw away the two strongest signals for realistic
  sale price: **price cuts over time** and **adverts disappearing** (a proxy for "sold").
  Asking price ≠ sale price; the daily delta is exactly what closes that gap.

---

## Part A — Data model changes (do first; every day of delay loses data) — ✅ DONE

Implemented on branch `feature/price-history-lifecycle`.

1. ✅ **`PriceObservation` table** — append-only log: `(ad_id, observed_at, price)`. Written on
   first sighting and only when price changes. `db.price_history(ad_id)` reads a trajectory.
2. ✅ **Lifecycle fields on `CarListing`**: `is_active`, `delisted_at`, and a derived
   `days_on_market` property. A **completed** run marks any in-scope advert it no longer sees
   as delisted (sold-proxy); a truncated run delists nothing.
3. ✅ **`seller_type`** column added (nullable) and **now parsed**. `parse_detail`
   reads the seller box (`div.author-info`): a verified-account marker (`_verified`
   class / `span.verified`) means `dealer`, its absence means `private`. The
   shop-link path is *not* used — some dealers link via `/items/author/<id>/`, the
   same shape private sellers use, so only the verified marker is reliable.
4. ✅ **`ScrapeRun` table** (`run_id, started_at, finished_at, n_seen, completed` + filter
   scope) so "not seen this run" is unambiguous, and delisting is bounded to exactly the
   make/model + year/price/mileage space the run covered (never touches other models).

Extras: `init_db` self-heals the columns on an existing DB; the CLI now reports
seen/delisted counts. 26 new tests (77 total, all passing).

Part A is complete — the `seller_type` selector (verified-account marker) is confirmed
from live pages and wired through `parse_detail` → `upsert_listing`.

---

## Part B — Pricing methodology (asking → realistic sale price) — ✅ DONE

Implemented in `analysis.py` (branch `feature/pricing-analysis`) as pure,
DB-independent functions plus a thin `estimate_from_db` wrapper; 26 tests.
Entry point: `analysis.estimate_sale_price(...)` / `analysis.estimate_from_db(...)`.

Two layers, because bazaraki only shows *asking* prices and we care about *sale* price.

### Layer 1 — Model the asking-price curve for a make/model — ✅

Implemented: `fit_price_curve` / `predict` (regression), `comparables` (cross-check),
`clean` / `is_usable` (outlier hygiene).

- **Primary: hedonic log-linear regression** on the model's rows:
  `log(price) ~ age + mileage_km` (+ `fuel_type` / `gearbox` dummies when N allows).
  Log-price because depreciation is roughly multiplicative; gives a smooth estimate *and* a
  prediction interval for any (year, mileage) query even where there's no exact comparable.
- **Cross-check: comparables median** — same model, year ±1, mileage band ±15k, take the
  median (robust to the odd damaged/typo listing). Large disagreement ⇒ N too thin ⇒ widen
  the band and flag lower confidence.
- **Outlier hygiene** before both: drop price/mileage physical impossibilities, flag
  dealer vs private.

### Layer 2 — Discount from asking to realistic sale (where the history pays off) — ✅

Implemented: `price_cut_factor`, `survivorship_adjustment` (residual-based, so it
controls for age/mileage), `sale_adjustment_factor` (combines both, 0.92 default
until history accrues). The combination is provisional — Part D recalibrates it.

- **Price-cut signal:** median % gap between an advert's *first* and *last* observed price
  (from `PriceObservation`).
- **Survivorship correction:** still-active listings are biased *high* (overpriced cars
  linger; good deals vanish). Compare the price distribution of *delisted-fast* adverts vs
  *still-active* ones.
- Combine into a **sale-adjustment factor** (e.g. "realistic sale ≈ 0.92 × asking-curve"),
  recomputed per model as data accumulates. Before history exists, start with a default
  (~5–10%) and let the data replace it.

### Query output (make, model, year range, mileage range)

- A **point estimate** (realistic sale price), a **range** (e.g. P25–P75 from the prediction
  interval), an **expected days-on-market at that price**, and **N + confidence**.
  For an import decision that's the actionable triple: what it sells for, how long it takes,
  how sure we are.

---

## Part C — Per-make/model Jupyter notebook — ✅ DONE

Implemented as `pricing_analysis.ipynb`: a single parameterized notebook (`MAKE`,
`MODEL`, `QUERY_YEAR_RANGE`, `QUERY_MILEAGE_RANGE` in the first code cell — tagged
`parameters` for papermill). It only *reads* the DB and draws; all estimation is
reused from `analysis.py` (Part B). Every history-dependent chart degrades
gracefully (prints a note instead of erroring) until daily runs accrue. Charts:

1. ✅ **Price vs. mileage** scatter, colored by year, with fitted regression curve + interval band.
2. ✅ **Price vs. year** (depreciation curve).
3. ✅ **Your-query marker** — plug in year/mileage range, show the estimate against the cloud.
4. ✅ **Price-history / cuts** — trajectories of adverts that changed price; distribution of cut %.
5. ✅ **Days-on-market vs. price percentile** — how underpricing buys speed.
6. ✅ **Market state over time** — active-listing count, median price by week (needs accrued history).

Plotting deps (`matplotlib`, `pandas`, `ipykernel`, `jupyterlab`) are in
`pyproject.toml`; the notebook is committed unexecuted (`uv run jupyter lab` to
render). Verified end-to-end against the live DB (Mazda CX-30, N=181).

---

## Part D — Suggested order

1. Schema migration + scraper changes for history & lifecycle (Part A) — **land ASAP**;
   it's the bottleneck on everything else.
2. An `analysis.py` module with regression + comparables + adjustment functions
   (reusable, testable — fits the existing test setup).
3. The notebook (Part C) consuming that module.
4. Let ~2–4 weeks of daily runs accumulate, then calibrate the Layer-2 adjustment from real
   delisting data.

---

## Part E

If advert from DB was delisted, visit advert by url to understand if it was expired (not sold) or it was actually sold
drop ads with status "in transit"

## Open questions

- **Sale-price ground truth.** bazaraki never confirms a sale, so Layer 2 is always an
  inference from delisting + price cuts. Any record of actual sale prices (even a handful)
  would anchor the adjustment far better than the survivorship proxy.
- **Scope:** one general notebook parameterized by make/model, or one saved notebook per
  traded model?