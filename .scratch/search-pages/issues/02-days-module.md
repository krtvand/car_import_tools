# 02 — A module that turns a search into day blocks

Status: done
Blocked by: 01

Both new pages are lists of **days**. This is where that is worked out, once.

## Do

New module `dashboard/days.py`:

- Find every run under `runs/` whose `lots.json` has `search.name == <name>`.
  All 87 runs on disk carry `search.name` and `trade_date`; no legacy fallback
  is needed.
- Assign lots to a day by **the lot's own `trade_date`**, not the run's, so an
  `--all-days` run (run-level `trade_date: null`) splits correctly.
- For each day, the **newest run covering that day wins** — a re-fetch of the
  same day supersedes the earlier one; never show both.
- `upcoming(name)` → days with `trade_date >= today`, nearest first.
- `past(name, days=7)` → days with `trade_date < today` inside the last 7 days,
  newest first. Nothing older is returned, ever.
- Each day carries: the date, its heading text, and the `report.Report` for the
  winning run (via `report.collect(run_dir)`), filtered to that day's lots.
- Heading text, exactly:
  - `Sat 12 Sep · 5 lots`
  - `Sat 12 Sep · no lots met the requirements — 31 dropped`
    (the drop count comes from `lots.json`'s `lots_filtered_out`)
  - `no upcoming day found` — the run narrowed to nothing upcoming
- A day nobody fetched is not represented. We store no auction calendar.

## Done when

`days.upcoming("toyota-harrier-z")` and `days.past("toyota-harrier-z")` return
the right days for the runs on disk, a day fetched twice appears once with the
newer lots, and unit tests cover the three heading cases from a fixture runs
directory.

## Comments

**2026-09-20 — implemented.** `dashboard/days.py` + `dashboard/tests/test_days.py`
(12 tests, all passing).

Public surface:

- `days.upcoming(name, root=None, today=None) -> list[Day]` — nearest first.
- `days.past(name, root=None, today=None, window=7) -> list[Day]` — newest first.
- `days.ever_fetched(name, root=None) -> bool` — for the index's "never fetched"
  row, which is not the same row as "no upcoming lots".
- `Day`: `.date`, `.run_dir`, `.lot_numbers`, `.dropped`, `.count`, `.heading`,
  `.report()`.
- `days.forget()` — drops the caches; autouse fixture in the tests.

Decisions taken while building it, beyond the ticket:

- **A day is derived from the lots *and* from the run's own `trade_date`.**
  Lots alone would lose the most informative block on the page: a fetch that
  found 31 cars and kept none has no lots to derive a day from, and that day
  must still print `Wed 16 Sep · no lots met the requirements — 27 dropped`.
- **`report()` returns `None` for a dateless day** rather than an empty report —
  there is nothing to render but the heading, and collecting would read the
  database and base64 a run's worth of sheets to say so.
- **The scan is `lru_cache`d for the life of the process** (`_scan`): one build
  writes seventeen pages off the same 87 `lots.json` files. Measured: 0.15 s
  cold, free afterwards. `_collect` is cached too but at `maxsize=4`, because a
  cached `Report` is megabytes of base64.
- **`dropped` is attributed only to the run's own `trade_date`**, never to the
  other days an `--all-days` run happens to carry — `lots_filtered_out` counts
  rejects on the narrowed day only (`fetch.FetchResult`).
- `RUNS_DIR` is defined here as well as in `index.py`; issue 05 rewrites
  `index.py` and should import it from here.

Fixed while testing: the window was `> today - 7`, which is six days. It is now
`>= today - 7` — seven days back is in, eight is gone from the UI entirely.

Checked against the real 87 runs: `toyota-harrier-z` → upcoming
`Tue 22 Sep · 3 lots`, past `Sat 19 Sep · 2 lots` … `Tue 15 Sep · 4 lots`;
`toyota-harrier-g` shows two all-dropped days in its window; `mazda-cx5` is
never-fetched. A day's report contains exactly that day's lots (`2 views` for a
2-lot day, all with `trade_date == 2026-09-19`).

`uv run pytest` — 908 passed. Note the CX-30 config test reported under issue 01
is no longer failing because **the operator deleted it**, not because the band
was changed; `mazda-cx30.toml` still ends at 50,000.
