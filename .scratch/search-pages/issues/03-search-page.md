# 03 — The search page

Status: done
Blocked by: 01, 02

`runs/searches/<name>/index.html`, in the operator's order.

## Do

1. Link back to `../../index.html`.
2. **Upcoming lots**: `days.upcoming(name)`, each day a heading then the report
   body verbatim — the three verdict groups in `report.Report.grouped` order,
   *fails a requirement* included, cards from `_card.html.j2`. With no upcoming
   day, an empty state carrying the literal command
   `uv run python -m banzai24 fetch --search <name>`. Never falls back to a
   finished day.
3. Link to `past.html`, directly under the upcoming block.
4. **Competitors**: this search's `competitors.SearchPanel`, rendered by a macro
   extracted from `competitors.html.j2`.
5. **Auction statistics**: this search's `statistics.SearchPanel`, likewise from
   `auction_statistics.html.j2`.
6. **Search params**: the raw text of `search.source`, verbatim, in a `<pre>`.

4, 5 and 6 are `<details>` collapsed by default, each `<summary>` carrying the
existing per-search summary line (`cli._summary` / `cli._statistics_summary`
reduced to one panel).

Everything inlined, every link relative.

## Done when

The page renders for all eight searches, the upcoming cards are
indistinguishable from the same lots in `report.html`, and the collapsed blocks
show their numbers without being opened.

## Comments

**2026-09-20 — implemented.** `dashboard/search_page.py`,
`dashboard/templates/search.html.j2`, `dashboard/tests/test_search_page.py`
(16 tests). Pages written to `runs/searches/<name>/index.html`, 0.84–4.10 MB
each, in 0.4 s for all seven enabled searches.

Panels extracted, as specified:

- `dashboard/templates/_competitors.html.j2` — `styles()`, `panel(panel)`,
  `legend()`.
- `dashboard/templates/_statistics.html.j2` — `styles()`, `panel(panel)`.
- `competitors.html.j2` and `auction_statistics.html.j2` now import them, so the
  old pages still render identically until issue 06 retires them.
- `competitors.panel_summary(panel)` and `statistics.panel_summary(panel)` — the
  line on the closed fold, keeping "0 asking less" apart from "not priced yet"
  and "0 sales" apart from "not measured yet".

**The one real obstacle: CSS class collisions.** The search page carries the
report's card rules and a panel's rules in the same document, and four names
meant different things in each — `.bad` prints a ✗ after a card's value (so
every competitor price would have rendered "€27,383 ✗"), `.verdict` paints a
card's rejection line pink, `.note` is a muted aside on a card and a yellow
warning box on a panel, and `.money` is a stacked bid on a card and a flex row
on a panel. Statistics collided on `.note` and `.sheet`.

Renamed on the **panel** side, markup and CSS together: `.band-money`,
`.band-verdict`, `.panel-note`, `.neg`/`.pos`, `.stat-sheet`. The card's meaning
is the older one and the one a reader already knows, and issue 01 promised the
report renders unchanged — so the panels moved out of its way.

Cascade order in `<style>`, deliberate and commented in the template: panel
styles, then the card palette and card styles, then the page's own chrome last.
Both panels and the card declare `:root` and `body`; the cards win the palette
because they are the block read every morning, and the page's own `.wrap`
(1100px, the report's width) beats the competitors page's 940px.

Other decisions:

- `_environment()` **borrows** `banzai24.report._environment()` and points its
  loader at both template directories. A second environment would be a second
  place to forget the `yen`/`km`/`check`/`verdict` filters.
- `jpy_per_eur` is not passed, which matches what `banzai24 report` renders by
  default — the euro-conversion note is a `--jpy-per-eur` override, not a page
  feature.
- The TOML is read from the path the panel recorded (`panel.source`), verbatim.
  A re-serialised copy of the parsed definition would drop every comment, and
  the comments are half of what that file says.
- The damage-code legend is on the page, folded, in the footer — the search page
  gets the reference table the report has, without it costing a screen.
- `SearchPage.lot_count` is every kept lot on the days ahead; issue 05's index
  row repeats exactly this number.

Verified on the rendered `toyota-harrier-z` page: 3 cards, 3 folded panels, 25
data URIs, no external `src` (the only absolute URLs are hyperlinks back to
banzai24), and the last `.wrap` and `body` rules in the cascade are the page's
own.

`uv run pytest` — 924 passed.

Left for issue 04: the `past.html` this page links to does not exist yet.
