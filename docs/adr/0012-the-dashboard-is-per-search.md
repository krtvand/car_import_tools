# The dashboard is per search

The dashboard is **one page per saved search**, and the index is a table of
contents. The two pages that panelled every search at once —
`runs/competitors.html` and `runs/auction_statistics.html` — are dissolved into
those search pages and stop being written.

## Why the global pages went

They were never really global. `competitors.build()` and `statistics.build()`
each already return one `SearchPanel` per enabled search; the page was a loop
over panels and nothing more. What the loop bought was a comparison nobody makes
— the Harrier Z's competitors are not weighed against the CX-30's — and what it
cost was that the panel you wanted was seventh on a 10 MB page.

The operator's morning question is "what is there to bid on for this car", and
answering it took the index, a run directory, a report, then two more pages to
see the same car's market. Everything about one car now sits on one page, in the
order it is read: lots first, because bidding is daily; competitors and auction
statistics folded away under them, because those are weekly.

## Why the index carries almost nothing

It lists the enabled searches, alphabetically by file name, with one line saying
how many lots wait on the upcoming day. It does not carry competitor counts or
benchmark counts — numbers on a page read every morning that change weekly are
numbers you stop seeing — and it does not sort by urgency, because a list that
reorders itself is one whose labels you stop reading.

It keeps exactly one piece of bad news: a search whose TOML will not parse gets a
red row with the parser's message. A missing row would be indistinguishable from
a search deliberately switched off, which is the same reasoning that dims an
unreported run rather than hiding it.

## What follows from it

**There is no cross-search view anywhere.** Not on the index, not on a summary
page. If comparing two searches ever becomes a real question, it is a new page
with a stated purpose, not a widening of this one.

**The UI forgets after seven days.** A search page shows the days ahead; its
past page shows the last seven days of lots, grouped by day, and nothing older.
122 MB of runs stay on disk and every lot stays in `auction.db` — they are simply
not reachable from a page. The archive was costing space on a page read every
morning and answering nothing.

**Days, not runs.** Nothing in the UI names a run. A run is a step in the
operator's morning, not an entity he thinks in, so the pages group lots by trade
date and two fetches of the same day collapse into one block with the newer
lots. This is why `CONTEXT.md` gained a **Search page** entry and deliberately
gained no **Run** entry.

**A day nobody fetched is not shown.** No auction calendar is stored, so an
unfetched day and a day with no auction are the same absence, and printing
either would be inventing a fact.

**Pages stay self-contained, and get big.** Sheets and photos remain data URIs
and every link is relative, so a page works wherever it is copied — which is
also most of what publishing to GitHub Pages will need. A search page runs to
several megabytes and a past page further. Accepted knowingly; optimised when it
starts to cost something, not before.

**The card is shared, not forked.** `report.html.j2`'s card macro and styles
become partials that both `banzai24 report` and these pages import. `banzai24
report` keeps writing per-run `report.html` and keeps its promise of no network,
no model call and no database write — the search page is a second reader of the
same renderer, not a second renderer.

**The build deletes nothing.** Pages for a search since disabled or deleted stay
on disk, unlinked and unrebuilt. They are cheap to ignore and the alternative —
a build that removes files — is a sharper tool than this needs.

Specified in `.scratch/search-pages/spec.md`.
