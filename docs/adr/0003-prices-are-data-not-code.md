# Prices are data, not code

Every price the world sets — exporter service fee tiers, the RoRo rate, VAT and
duty, the FX haircut, bank charges, the fixed Cyprus bills, road tax, resale
costs — lives in `price_calculator/inputs/costs.toml` and is loaded into a
frozen `CostBook`. `price_calculator/calculator.py` contains **no prices at
all**: it takes a cost book as a required argument, alongside the rates it
already took, and keeps only the arithmetic and the reasoning about it.

A cost book is stamped into each run as `costs.json`, beside `rates.json`, and
a run that has no stamped book shows no landed cost — the rule already in force
for rates.

## Why

The prices were named constants in the middle of a 375-line module whose prose
argues about VAT asymmetry and survivorship bias. Editing a supplier's price
list therefore meant editing code, and the module's own history shows what that
costs. Commit `908fe4b` raised the five service fee tiers; four lines above
them, the comment explaining the band boundaries still read *"¥1,000,000 pays
¥56,000 and ¥1,000,001 pays ¥71,000"*. `price_calculator_spec.md` carried a
third copy of the same table, also unchanged, also wrong. And the test suite
went red in twelve places — including `test_the_reference_car_matches_the_sheet`,
which asks whether the port drifted from the spreadsheet and has nothing
whatever to say about what an exporter charges this month.

That is one defect with three faces: a number that is edited monthly was stored
in the same place as reasoning that is true for years, so every price change
invited a documentation lie and a false test failure.

Rates were already outside the module, for a reason stated in `write_rates`: a
landed cost is a statement about a moment, and re-opening August's report in
September must not rewrite August's decision. Fees are the same claim. Leaving
them as import-time constants meant the RoRo rate going up silently reprised
every report ever rendered.

## Alternatives

**A separate constants module** (`costs.py`) would have shortened
`calculator.py` without making the edit stop being a code edit, and would still
have been read at import time — so it fixes the length complaint and neither of
the real ones.

**Effective-dated rows**, so June's run is priced from June's entry, is the
logical end of the reproducibility argument. It was rejected as machinery: a
date on every line and a "which book was in force?" lookup at every call site,
to serve a workflow where cars are priced and bid on the same morning. Stamping
the whole book into the run buys the same reproducibility without it.

**Keeping last-known-good defaults in code** as a fallback for an unreadable
file was rejected because it recreates the two-homes problem exactly, in the
copy that nothing ever exercises and so nothing ever updates.

## Consequences

**A bad cost book fails the whole run, loudly.** `CostBookError` is raised at
load and nothing is priced. This deliberately breaks the pattern
`model_specs.csv` follows, where a bad row degrades to a `reason` string on one
card, and the difference is blast radius: a missing model spec costs one car,
while a fat-fingered comma here makes every number on the page wrong. Forty
cards silently reading "no landed cost" is a worse morning than one line of
stderr.

**Old runs show no landed cost.** Twenty-one of the twenty-two runs on disk
already lack `rates.json` and already show none, so this costs one run today —
but the rule is now enforced twice and a run must carry both stamps.

**`with_freight_insurance` is gone.** The flag existed only to zero a price, and
a book that sets `insurance_usd = 0` does that already.

**The tests are frozen at the port's prices.** `REFERENCE_COSTS` in
`test_calculator.py` carries the ¥56,000 tiers the spreadsheet had at the port,
because §6's worked example is a statement about the sheet in January, not about
today's price list. A separate test loads the real `costs.toml` and asserts only
what is true of any valid book. The risk this accepts: nothing now checks the
shipped prices against reality, and a wrong RoRo rate is as green as a right
one. That was already true — it was just wearing a false alarm.
