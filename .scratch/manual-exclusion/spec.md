# Manually excluded adverts: dismissed, but still on the page

Status: ready-for-agent

Some adverts pass every declared filter and undercut the band's cyprus sell
price, and are still not competition: the car is available only by order, the
seller is a broker, the advert is a duplicate. Today the only way to get rid of
one is a `[competitors]` filter, which deletes it from the page — and a row that
has vanished is a row you re-investigate next week, because bazaraki still shows
it under the same filters and it still looks like a true competitor.

This adds a per-advert mark that does the opposite of a filter: **it keeps the
advert on the page and strips it of its meaning.**

Settled by grilling on 2026-08-29. Every decision below was put to the operator
and answered.

## The distinction this is built on

| | filter (`[competitors]`) | manual exclusion |
| --- | --- | --- |
| what it is | a **rule**, true of adverts not yet seen | a **judgement** about one advert already read |
| where it lives | the search's .toml | a column on `carlisting` |
| effect | the advert is deleted from the page | the advert stays, dimmed and uncounted |

A phrase exclusion has to stay true for the next advert too. "Available only by
order" is discovered by reading one advert, sometimes by messaging the seller;
it cannot be spelled as a rule. Rules that are really judgements rot into
unreadable phrase lists.

## The mark

One nullable column on `carlisting`:

```sql
ALTER TABLE carlisting ADD COLUMN manual_exclusion_reason VARCHAR
```

NULL is the normal case, processed exactly as today. Any non-NULL value marks
the advert excluded, and the value is the reason:

```sql
UPDATE carlisting SET manual_exclusion_reason = 'order only - dealer confirmed 29 Aug'
WHERE ad_id = 6631913;
```

Text rather than a boolean, because the mark is applied by hand and read months
later: a boolean loses the only thing that makes it auditable.

**Named `manual_exclusion_reason`, not `excluded_reason`.** "Exclude" is already
taken — `exclude_colours`, `exclude_phrases` and `_passes` all exclude by
*deleting*. This column does the opposite under the same word, so the word
"manual" carries the whole distinction and is worth the extra characters.

No API, no UI for setting it. Marks are written straight into `bazaraki.db`.

### It survives the crawl

`db.upsert_listing` only assigns keys present in the scraper's dict, so a column
the scraper never writes is never clobbered. No further work is needed to make
the mark durable, and none should be added.

### Migration

Add the column to `db._ADDED_COLUMNS`, which `init_db()` applies idempotently to
an existing table. Same mechanism as `seller_type` and `description`.

## Decisions

### A fact about the advert, not about a band

One column, global: mark an ad_id once and it is excluded wherever it would have
appeared, in every band of every search. Per-band judgements already have a
home — the `[band.competitors]` filters, which is why the RAV4's AXAH54 band
drops adverts the AXAH52 band deliberately keeps.

### The mark asserts nothing beyond "not competition"

Deliberately broad: any reason at all. The operator chose this over a narrow
"not purchasable in Cyprus" meaning, and the cost is recorded here so it is not
rediscovered as a bug.

**A broad mark cannot be propagated into the pricing model.** "Order only" and
"wrong trim" imply opposite things about what the Cyprus market is worth, so one
flag mixing them must never reach `CyprusMarket` or `bazaraki.analysis`. Those
fit an asking curve over ~200 adverts; a single dismissed advert is not evidence
against it, and wiring the flag in would silently re-price every band on the
page as a side effect of a colour change — the failure `docs/adr/0007` already
refused for `days_on_market`.

So the rule is: **everything the competitors panel shows or counts respects the
judgement; the pricing model never sees it.**

* `_rows_for` — the advert is rendered, but not as a competitor (below)
* `considered` — does not count it
* `band.competitors|length` and `dashboard.competitor_count` — do not count it
* `_stuck_above` — does not count it, even though it sits above the sell price;
  same panel, same judgement
* `CyprusMarket`, `bazaraki.analysis`, every Cyprus estimate — untouched

### It is not a competitor, so it is in no count

If a band's only under-price adverts are all marked, the page prints the good
news — *"Nothing under EUR 18,822 - of N adverts inside this band's competitor
bounds"* — **and still shows the marked rows underneath it**. Those two are an
`{% if band.competitors %}` / `{% else %}` today and cannot currently both
happen; that branch has to be restructured. Anything else means a dismissed
advert silently holds the competitor count above zero, which is the bug being
fixed.

### It stays inline, at its price

Rows are cheapest-first because that is queue order. A dismissed advert has no
queue position at all — but it keeps its place in the price order anyway,
because the mark's job is **recognition**: on bazaraki's results page under the
same filters you want to see that the EUR 17,400 one is already dealt with. A
separate "manually excluded" block below the table was rejected — it turns
"already dealt with" into a second list to cross-reference.

### How the row reads

Two cells assert things a dismissed advert cannot claim: `under by` (it is not
undercutting you) and the coloured age mark (it has no queue position, so green
and red are both lies).

* whole row dimmed, price struck through
* `under by` and the age mark both `-`, with no colour class
* the reason printed in italics after the title, in the advert cell
* one sentence added to the legend

**Not a fourth mark constant.** `FAIR` / `OVERPRICED` / `UNPROVEN` are verdicts a
price has earned; this row has withdrawn from that judgement rather than earned a
new verdict. Carry it as a separate field on `CompetitorRow` (the reason string,
`None` when normal), never as a fourth value of `mark`.

### The ad_id goes on the page

Printed small and muted after the title. It is the only affordance the
edit-the-database-by-hand workflow needs: today the id exists on the page only as
the leading digits of a URL, so every marking session starts by squinting at a
link. Nothing further — no copy button, which is the thin end of building the API
that was explicitly not wanted.

### A marked advert disappears when it is delisted

Delisted competitors normally stay for `COMPETITOR_HISTORY_DAYS` because how fast
they went is evidence. A dismissed advert offers no such evidence, and once it is
off bazaraki nobody will ever see it there and need reminding. So a row that is
both marked and gone is dropped from the table entirely.

The column stays set. If the same ad_id returns, the mark returns with it. (A
relist under a *new* ad_id does not — the same gap already recorded in
`.scratch/relisting-detection/spec.md`.)

## Glossary and decision record

`CONTEXT.md` gains **Manually excluded**.

`docs/adr/0007-competitors-are-a-queue.md` gains a *Withdrawn from the queue*
section. Not a new ADR: one column and one filter is not hard to reverse, and the
decision is a footnote on 0007's model rather than a model of its own. What earns
the paragraph is the inversion — in this repo every other exclusion deletes, and
this one preserves.
