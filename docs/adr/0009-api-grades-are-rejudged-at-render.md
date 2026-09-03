# The `[api]` grade rules are re-judged when the report is built

A lot's trim line is checked twice: once by `banzai24.lot_filters` when the fetch
decides what to keep, and again by `banzai24.requirements` every time a report is
rendered. The second check produces a `trim` requirement, so a lot the search's
`model_grades` / `exclude_model_grades` now disown sits in *fails a requirement*
with the offending word printed against it.

Only the trim line. The other two `[api]` criteria — `body_model_code` and
`exclude_colours` — are still judged once, at fetch.

## Why

A run is a record of one morning. A search definition is not: it is read live
every time the page is built, which is the rule
`docs/adr/0004-bid-prices-are-read-live.md` set for max bids and
`docs/adr/0005-one-search-file-two-parsers.md` set for the fetch bounds. The two
therefore drift by design, and the trim line is where the drift costs money.

One car is four search definitions split on nothing but that line
(`cars/reference/harrier-grades.md`): the four Harrier trims share a chassis
code, share a model spec, and are about ¥1.5M apart. So the trim line is the one
`[api]` value an operator genuinely re-tunes between a fetch and the report they
read — Monday's file bans nothing, Friday's bans `G`, and Monday's G is still on
Friday's page with a bid price on it and nothing anywhere saying it should not
be. A chassis code and a banned colour are written once and left alone; a lot
that reached the page under either is not evidence of anything, and re-judging
them would be ceremony.

The section a requirement is declared in says *who is able to check it*, not how
much it matters. `[api]` meaning "checked at fetch and never mentioned again"
was reading it as the latter.

## Consequences

**A blank trim line passes.** That is the exclusion's own rule — an exclusion
only ever drops what it can positively recognise — and about one hybrid RAV4 in
nine names no grade. Overturning it here would make the report disagree with the
fetch that kept the lot. The report says it another way instead: the card carries
the empty line with the rule beside it and wears an `unstated-trim` badge, which
sorts it up without moving it out of its group. The answer to that badge is in
the lot photographs, which is the only place it has ever been.

A positive `model_grades` list keeps pointing the other way, again matching the
fetch: a lot with no trim line **fails**, because a search that names the grade
it wants has not been shown this is it.

**The sheet does not outrank the API here**, which is a deliberate exception to
`docs/adr/0001-sheet-outranks-api.md`. The sheet's グレード box is in Japanese
and the search's grade words are the auction *list's* romaji; matching one
against the other needs a translation table, and `cars/inputs/trims.toml` refuses
to hold one on the grounds that it would be a second copy of the search files
that drifts from them. So the two readings sit one row apart on the card and the
comparison is the operator's. If that ever proves too slow, the fix is a `key`
on the search's grade rules pointing at a trim in `trims.toml` — one join, not
two tables — and this ADR should be superseded rather than stretched.

**`banzai24.stats` and the dashboard's benchmark panels are unaffected**, though
they now pass the same filter into `judge`. Both already apply `LotFilters` to
every lot before judging it, so the new check can only agree with them.
