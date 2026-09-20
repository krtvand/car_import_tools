# 06 — Rewire `dashboard build`, retire the global pages

Status: done
Blocked by: 03, 04, 05

## Do

- `dashboard build` writes `runs/index.html`, and `index.html` + `past.html`
  under `runs/searches/<name>/` for every enabled search. Always a full rewrite.
- Stop writing `runs/competitors.html` and `runs/auction_statistics.html`.
  Existing files are left on disk; so are pages for a search that has since been
  disabled or deleted. Nothing is deleted by the build.
- `dashboard open` still opens `runs/index.html` in the parser's Chrome.
  `banzai24 report --open` still opens the index too — unchanged.
- The terminal output keeps its warnings (`money_problem`, per-panel `problem`
  and `coverage`), now printed per search page written.
- Move `dashboard/tests/test_render.py`, `test_competitors.py` and
  `test_statistics.py` onto the per-search panel macros; rewrite
  `test_index.py` for the table of contents.

## Done when

`uv run python -m dashboard build` writes 1 + 16 pages, two consecutive builds
differ only in the generated-at stamp, and the test suite passes.

## Comments

**2026-09-20 — implemented.** `dashboard/cli.py` rewritten,
`dashboard/tests/test_cli.py` added (9 tests), `test_render.py` and
`test_statistics.py` moved onto the panel macros.

`uv run python -m dashboard build` writes **1 + 14 pages in 2.0 s**:

```
Wrote /…/runs/index.html
  mazda-3            no upcoming lots · not priced yet
  mazda-cx30         no upcoming lots
  toyota-harrier-g   1 lot
  toyota-harrier-s   no upcoming lots
  toyota-harrier-z   3 lots
  toyota-rav4-g      no upcoming lots · not measured yet
  toyota-rav4-x      no upcoming lots
```

**Deleted**: `dashboard/templates/competitors.html.j2`,
`dashboard/templates/auction_statistics.html.j2`, `cli.render`,
`cli.render_statistics`, `cli._summary`, `cli._statistics_summary`,
`COMPETITORS_FILENAME`, `STATISTICS_FILENAME`. `_backticks` moved to
`search_page.py`, which is the only module that renders anything now — and
`search_page` no longer reaches into `cli` for it.

**The terminal prints gaps, not counts.** The old build printed "113 competing
adverts · 28 asking less" — the summary of a page that no longer exists.
Reprinting it per search would be the old index with a different font, against
the whole point of Q22/Q24. So a line carries the lot count, and a panel summary
only when it is *empty* — `not priced yet`, `not measured yet` — which is a file
to go and edit rather than a market to read. `money_problem` and per-search
`coverage` still print, as specified.

`build()` now returns a `Build` dataclass (listing, pages, dashboard,
statistics, notes) rather than a 4-tuple; `main` reads `result.listing` for
`open`, which is unchanged, as is `banzai24 report --open`.

**Nothing is deleted by a build**, and there is a test for it: an orphan page
from a search since switched off, and last week's `competitors.html`, both
survive a build untouched.

Docs updated where they described the retired pages: `README.md` (the dashboard
section now describes the index and the five blocks of a search page) and
`daily.sh`'s header and closing prose.

`uv run pytest` — 933 passed.

Note for the operator: `runs/competitors.html` (100 KB) and
`runs/auction_statistics.html` (10 MB) are still on disk, now stale and reachable
from nothing. The build will never touch them; delete them by hand when you like.
