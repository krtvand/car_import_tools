# Bid prices are read live

Amends `0003-prices-are-data-not-code.md`.

The max bids moved out of `banzai24/inputs/bid_prices.csv` and into the search
definition, as its `[[band]]` list. The search file is loaded **by name** on
every render — `search.for_run` has always preferred the named file to the run's
own provenance copy, so that re-tuning a requirement re-judges an old morning
for free — and the bands now ride along with that.

So: **re-rendering an old report re-prices it at today's max bids.** Raise the
CX-30's band from ¥1,805,000 to ¥1,900,000 in September, re-run `report` on the
17 August run, and every margin on that page changes. The report no longer shows
the number you bid that morning.

The cost book and the exchange rates keep stamping, unchanged. The bands are
still *recorded* in `lots.json` provenance as an audit trail, and are read back
only when the named file has since been renamed or deleted.

`price_calculator.sources.money_for_today` is part of this decision: the
dashboard has no run directory, and prices its bands at today's rate and today's
book, falling back to the newest stamped pair with a note when the network is
down.

## Why

ADR 0003 argued that a landed cost is a statement about a moment, and that
re-opening August's report in September must not rewrite August's decision. That
argument is still right about **costs and rates**, and it is why they still
stamp. It is not right about max bids, and the difference is who moves them.

An exporter raises the RoRo rate without telling you. The ECB moves overnight.
Those change *under* you, so a report that silently adopted them would be
rewriting a decision you made on facts you had. A max bid changes because **you
changed it** — it is your own judgement about a car, typed by you, and the
version you want to see is the one you hold now. Freezing it into the run would
mean the table you spent the morning tuning does not apply to the morning you
tuned it for.

The merge is what forced the question. Bands and requirements now live in one
file, and requirements have always been read live for exactly this reason:
"re-tuning a requirement and re-rendering this morning's run costs nothing." A
file with two precedence rules inside it — half read live, half read from the
run — would be a rule nobody could hold in their head while editing.

The merge itself was not optional. `bid_prices.csv` and the .toml were two
declarations of the same mileage ranges, and they had already drifted twice: the
CX-30 fetched to 55,000 km while the table priced to 60,000, and the CX-5
fetched to 70,000 km with no bid price above 60,000 — lots that were fetched
every morning and could never be priced. Deriving the fetch bounds from the
bands makes that class of drift unrepresentable.

## Alternatives

**Split precedence inside the one file** — requirements live, bands from
provenance — gives the reproducibility ADR 0003 wants without a second file. It
was rejected as a rule that cannot be taught: the two halves look identical in
the editor, and the first time someone re-runs a report after re-tuning a bid
and sees the old number, they will file it as a bug.

**Stamping the bands as `bids.json`**, beside `costs.json`, is the same
behaviour through machinery that already exists and is already understood. It
was the recommended option and was rejected on the same grounds — it makes the
*record* better and the *daily loop* worse, and the daily loop is where the
decisions actually get made.

**Keeping `bid_prices.csv`** and leaving the search file alone would have
avoided the question entirely, at the price of keeping the drift that motivated
the merge.

## Consequences

**`LandedPricer`'s docstring was false and has been rewritten.** It argued, at
length, that re-computing at today's prices "would mean this page quietly
disagreeing in September with the decision you made in August, which is worth
less than a blank line." That is still the rule for the cost book it describes,
and no longer the whole story on the page. Leaving the paragraph standing would
have been exactly the failure ADR 0003 was written about: reasoning that is true
for years stored beside a number that changes monthly, and going stale the first
time the number moved.

**A run's provenance is now the only record of what you actually bid.** It is
written and never rendered. If it ever matters what a car was priced at on the
day, that is where to look — `lots.json`, under `search.bands`.

**The dashboard is not a record and does not pretend to be.** It answers "should
I be buying this car this morning", so it prices at today's money and prints the
rate's timestamp on the page. `read_rates(run_dir)` and `read_costs(run_dir)`
still refuse to invent prices for a past run; `money_for_today` is a separate
door, for a page that has no run behind it.
