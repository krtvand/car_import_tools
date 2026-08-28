# Market state panel

Status: needs-triage

A second panel beside the competitors table, answering the question the
competitors table deliberately cannot: **how hard is this car to sell at all?**

## Why it is a separate panel

Settled by grilling on 2026-08-28. A **competitor** is one advert with a better
offer than yours — cheaper than your cyprus sell price, inside the band's
competitor bounds. That definition is worth keeping sharp, so the competitors
table stays scoped to it.

But the operator's actual question — *how difficult will it be to sell at this
price* — is answered mostly by adverts the competitor definition excludes. For
the Mazda CX-30 2023 band (cyprus sell price €18,822) on 2026-08-28:

```
under €18,822:                                    7 adverts   ← whole competitor table
in-bounds, live, listed over 30 days, above it:      29
```

Twenty-nine cars have sat unsold above that price for more than a month. None can
ever appear in the competitors table, so that table shows seven rows and says
nothing about the frozen band just above them. Widening the table to fix this
would bury the rows that matter under the 150-odd in-bounds adverts that do not —
hence a second panel rather than a looser filter.

(The Toyota RAV4 band that prompted this work has a stuck-above count of 0. Its
bazaraki history is only 19 days deep and no in-bounds advert reports a publish
date older than 30 days, so the question this panel answers is currently
unanswerable for that car — worth knowing before designing against it.)

## The queue model it is built on

Buyers work the market cheapest-acceptable-first. A competitor is someone ahead
of you in that queue. Cheap stock clearing quickly means the queue ahead of you
moves, so you sell later rather than never — but that is only good news if the
queue *behind* you is moving too. Stuck cars above your price are the market
saying it is not.

## What it should show

Not settled. Starting points, in the operator's stated order of interest — he
does not want detailed sell statistics, so this should stay a small number of
readable facts, not a table of every advert:

* how many in-bounds adverts sit above the cyprus sell price and have been
  listed longer than `OVERPRICED_AFTER_DAYS`
* how those break down either side of the cyprus **estimate**, which is the
  other line the band already knows
* whether anything sold *above* the cyprus sell price in the window — the single
  fact that distinguishes "I am next in the queue" from "the market ends below
  me", and which nothing on the page currently shows

## Interim measure already shipped

The competitors table carries one line under it — *"N adverts above your price
have been listed over 30 days"* — agreed on 2026-08-28 as the cheapest thing
that stops the table implying an easy sale. This panel replaces that line.

## Depends on

`.scratch/relisting-detection/spec.md`. Every count here reads delisting as a
sale, and roughly one delisting in four across the searched cars is a relist.
