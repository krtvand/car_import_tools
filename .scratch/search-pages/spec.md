# Search pages: one page per saved search

Status: done

The dashboard stops being three pages panelled by search and becomes **one page
per search**, with an index that is a table of contents. The cars you can still
bid on sit at the top of that page, rendered as richly as `report.html` renders
them, and everything else you know about the search sits under them in the order
you read it.

Settled by grilling on 2026-09-20; implemented the same day, issues 01–06. Every decision below was put to the operator
and answered; the alternatives that lost are recorded where the reason matters.

## Why

The index answers "which run was that?" — a question nobody asks. The morning
question is "what is there to bid on for the Harrier Z", and answering it today
means the index, then a run directory, then the report; then a second page for
competitors and a third for statistics, each of which buries this search's panel
among seven others. The panels were already per search. Only the pages were not.

## The shape of it

```
runs/index.html                              the eight enabled searches, nothing else
runs/searches/toyota-harrier-z/index.html    the search page
runs/searches/toyota-harrier-z/past.html     the last 7 days of lots
runs/<stamp>_<CAR>/report.html               unchanged, still written by `banzai24 report`
```

`runs/competitors.html` and `runs/auction_statistics.html` stop being written.
Their content moves, unchanged, onto each search page.

## The index

A table of contents and nothing more.

- One row per search with `[dashboard] enabled != false`, **sorted alphabetically
  by file name**, labelled by file name — `toyota-harrier-z` is what you type at
  `--search`, it sorts the three Harriers together, and no derivation can go
  wrong on a file that stops following the convention.
- One line of summary per row, and it is only ever about lots waiting:
  - `toyota-harrier-z — 5 lots`
  - `toyota-harrier-z — no upcoming lots`
  - `toyota-harrier-z — never fetched`
- A search whose TOML will not parse gets a row in red carrying `load_all`'s own
  message: `toyota-rav4-g — file will not load: <problem>`. Silence would make a
  broken file indistinguishable from one switched off.
- **Nothing else.** No competitor counts, no statistics counts, no run list, no
  "other runs" list, no sort by urgency — a list that reorders itself each
  morning is one you stop reading the labels of.

The count is **every kept lot for the upcoming day**, the same number the day
heading shows, so the two pages can never disagree. Not "biddable lots": an
*unconfirmed* lot is one you resolve by opening the sheet, and a *fails a
requirement* lot is one you re-judge when you loosen a requirement. The operator
decides which is a bid, not the count.

## The search page

In this order, top to bottom:

1. **A link back to the index.**
2. **Upcoming lots** — full cards, grouped by day.
3. **A link to past lots**, directly under the upcoming block. It sits here and
   not in the header because it is the same subject split by time, and because
   under an empty upcoming block it is the page's only useful link.
4. **Competitors** — this search's existing `competitors.SearchPanel`.
5. **Auction statistics** — this search's existing `statistics.SearchPanel`.
6. **Search params** — the file's raw TOML, verbatim.

Blocks 4, 5 and 6 are `<details>` **collapsed by default**, each with its summary
line visible on the closed element (`14 competing adverts · 3 asking less`,
`12 cheapest acceptable sales`). Weekly reading should not cost a scroll past
4 MB of cards every morning — and those summary lines, dropped from the index,
belong on the page they describe anyway.

### Day blocks

A **day** is a trade date. It is the only unit on these pages: there is no "run"
in the UI, because a run is a step in the operator's flow and not a thing he
thinks about. Two fetches of the same search for the same day are one day block;
the newest run covering that day wins.

Lots are assigned to a day by **the lot's own `trade_date`**, not the run's, so a
`fetch --all-days` run (whose `lots.json` has `trade_date: null`) splits across
day blocks correctly.

A heading must make unmistakable **which day these lots are for, or were not
found for**:

| case | heading |
|---|---|
| lots kept | `Sat 12 Sep · 5 lots` |
| fetched, everything dropped | `Sat 12 Sep · no lots met the requirements — 31 dropped` |
| fetched, the site had nothing upcoming | `no upcoming day found` |

A day nobody fetched is **not shown**. We store no auction calendar, so a day
with no run is indistinguishable from a day with no auction, and inventing the
difference would be inventing a fact. (Wanting those gaps visible is a separate
ticket and a separate fetch.)

**Later trade dates are not mentioned.** `fetch.py` knows when lots exist on days
beyond the one it narrowed to (`fetch.py:171`); the page says nothing about them.

### Upcoming, and the empty case

Upcoming is `trade_date >= today`, nearest day first — normally exactly one day,
because `fetch` narrows to `nearest_trade_date` unless given `--all-days`.

With no upcoming day the block is an empty state naming the fix:

> No upcoming lots. `uv run python -m banzai24 fetch --search toyota-harrier-z`

and the link to past lots below it. It **never** falls back to showing the last
finished day's cards: the whole point of splitting future from past is that the
two must never look alike, and a page that quietly shows Tuesday's cars is how
you bid on a car that sold on Tuesday.

### The cards

The upcoming block is the report body **verbatim** — the same three verdict
groups in the same order, *fails a requirement* included, because re-judging at
render is the reason that group exists (`docs/adr/0009`). Sheets and photos are
data URIs, exactly as in `report.html`.

## The past page

`runs/searches/<name>/past.html`: day blocks for the **last 7 days**, newest
first, same headings, same cards, same template. Only days that were fetched.

Nothing older than 7 days appears anywhere in the UI. The runs stay on disk and
the lots stay in `auction.db`; they simply stop competing for space on a page
read every morning. There is no archive list and no link to one.

## Mechanics

**The card is shared, not copied.** `banzai24/templates/report.html.j2` splits
into a card partial and a stylesheet partial that both `report.html` and these
pages import. `banzai24 report` keeps writing `runs/<run>/report.html` and keeps
its promise of no network, no model call, no database write.

**The panels are already per search.** `competitors.build()` and
`statistics.build()` each return one `SearchPanel` per enabled search; only the
templates need splitting into a macro that renders one panel. No builder is
restructured.

**Pages stay self-contained** — every image inlined, every link relative. A
search page will run to several megabytes and the past page further; that is
accepted, and optimised when it starts to bother the operator rather than
before. It is also most of what an eventual GitHub Pages export needs, which is
explicitly **not** in this work.

**`dashboard build` writes** `runs/index.html` plus `index.html` and `past.html`
under `runs/searches/<name>/` for every enabled search — always a full rewrite,
staleness being the only failure mode.

**A disabled search's pages are left on disk.** They stop being rebuilt and stop
being linked; they are not deleted.

**`dashboard open` and `banzai24 report --open` both still open the index.**

## Validation

- Eight searches, eight rows, alphabetical; break one TOML and its row goes red
  with the parser's message rather than vanishing.
- A search page's index-row count equals its upcoming day heading count.
- `dashboard build` twice in a row produces byte-identical pages but for the
  generated-at stamp.
- A day fetched twice appears once, with the newer run's lots.
- With no upcoming day, the top block is the empty state and the past link
  still works.
- `runs/competitors.html` and `runs/auction_statistics.html` are no longer
  written; their existing tests move to the per-search panel macros.
- `report.html` still renders identically after the template split.

## Costs accepted

- **No cross-search view.** Nowhere shows every competitor or every benchmark at
  once. The eight-panel pages are gone and the index deliberately carries none of
  their numbers.
- **The UI forgets after 7 days.** 122 MB of runs on disk, none of it reachable
  from a page. Finder is the archive.
- **Eight renders of each panel per build**, where there was one — the
  statistics walk is the expensive part and it already runs per search.
- **Megabyte pages**, by choice, in exchange for pages that work anywhere they
  are copied.

See `docs/adr/0012-the-dashboard-is-per-search.md`.
