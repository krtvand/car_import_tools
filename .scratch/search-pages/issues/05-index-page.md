# 05 — The index becomes a table of contents

Status: done
Blocked by: 02

`dashboard/index.py` is rewritten. `RunEntry`, `recent()` and the run list go.

## Do

- One row per search with `[dashboard] enabled != false`, sorted alphabetically
  by file name, labelled by file name, linking to
  `searches/<name>/index.html`.
- One summary line per row, only ever about waiting lots:
  `— 5 lots` / `— no upcoming lots` / `— never fetched`. The count is every kept
  lot on the upcoming day, from `days.upcoming()`, so it always equals the day
  heading.
- A search whose TOML will not parse gets a red row carrying `load_all`'s
  message.
- Delete: the run list, the unreported-run dimming, the orphan/"other runs"
  idea, and the two `panel-link` cards to the old global pages.

## Done when

Eight rows, alphabetical, no numbers on the page other than lot counts; breaking
one TOML turns its row red rather than removing it.

## Comments

**2026-09-20 — implemented.** `dashboard/index.py` rewritten,
`dashboard/templates/index.html.j2` rewritten, `dashboard/tests/test_index.py`
rewritten (16 tests — the old 22 were all about the runs list).

`runs/index.html` is now **3.8 KB**: seven rows, alphabetical, nothing else.

Gone, as specified: `RunEntry`, `recent()`, `_run_dirs()`, `_parse_name()`,
`_lot_count()`, the unreported-run dimming, and the two `panel-link` cards to
`competitors.html` and `auction_statistics.html`. A test now asserts that none
of those strings can come back onto the page.

The surface is `Row(name, lots, upcoming, problem)` with `.href` and `.summary`,
plus `rows()`, `render()` and `write()`.

Decisions:

- **`lots` is `None` for never-fetched**, and `upcoming` is a separate boolean,
  so the three absences stay apart in the data rather than only in the wording:
  `never fetched` is a morning you forgot, `no upcoming lots` is a quiet week,
  and `file will not load: …` is neither.
- **A row with nothing waiting is marked on the row** (`.quiet` — no card
  background, muted name) rather than only in its text, so "nothing to bid on
  anywhere today" is one glance down the list. This is presentation, not the
  sorting you turned down in Q22: the order never moves.
- **`index.RUNS_DIR` is now re-exported from `days`** rather than defined twice,
  as issue 02 flagged.
- `dashboard/cli.py`'s call was updated to `index.write(runs_dir)` — it no longer
  passes the two summary lines. The rest of `cli` is issue 06.

`uv run pytest` — 924 passed (the suite lost six tests with the old index).
