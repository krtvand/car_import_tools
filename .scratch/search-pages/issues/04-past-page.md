# 04 — The past-lots page

Status: done
Blocked by: 01, 02, 03

`runs/searches/<name>/past.html`: `days.past(name)` — day blocks for the last 7
days, newest first, same headings and same cards as the upcoming block.

## Do

- A link back to the search page and to the index.
- Nothing older than 7 days: no archive list, no link to one, no "older runs"
  anywhere.
- Only days that were fetched.

## Done when

A search with runs on five of the last seven days shows five day blocks, and a
run from 3 weeks ago appears on no page at all.

## Comments

**2026-09-20 — implemented.** `dashboard/templates/past.html.j2`,
`PastPage`/`collect_past`/`render_past`/`write_past` in `dashboard/search_page.py`,
6 more tests in `dashboard/tests/test_search_page.py` (22 there now).

**The day block moved into a shared partial**, `dashboard/templates/_days.html.j2`,
rather than being written twice: `styles()` (the page chrome and the `.day`
heading), `blocks(pairs)` (the day-by-day loop, cards and verdict groups) and
`legend(damage_codes)`. `search.html.j2` was rewritten to import it, so a verdict
group added to a day now appears on both pages or on neither — the same
one-renderer rule that made `_card.html.j2` worth extracting in issue 01. The
search page's rendered output is unchanged.

`_car_for(name, dashboard, stats)` was factored out at the same time: both pages
want "Toyota Harrier" rather than the file stem, and either panel may be missing.

Written for all seven enabled searches: **22.7 MB of pages in 0.7 s**. The
`toyota-harrier-z` past page is the big one at 5.76 MB — 5 day blocks, 21 cards,
105 inlined images. `toyota-harrier-s`, `toyota-rav4-g` and `toyota-rav4-x`
come out at 0.01 MB: nothing in their window, so they carry the empty state.

The empty state says the honest thing rather than the comfortable one: *no day
in the window was fetched, and days nobody fetched are not shown — no auction
calendar is stored, so a quiet market and a morning you skipped look the same
from here.*

Verified on the rendered page: exactly two links leave it, `index.html` (the
search page) and `../../index.html`; no `report.html`, no archive, nothing
reaching a run older than the window.

`uv run pytest` — 930 passed.
