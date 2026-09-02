# A car is not a price

What is true of a **car** — its key, its make and model, its dimensions, its
CO₂, and the trims it was sold in — lives in `cars/`, a package that imports
nothing else in this repo. `searches` imports it, and `banzai24`, `bazaraki`,
`price_calculator` and `dashboard` import both. `cars/` is now the dependency
root; ADR-0005's arrangement is otherwise unchanged.

Three things moved into it and one was written new:

* `searches/cars.py` → `cars/definitions.py` — the key, the make, the model.
* `price_calculator/inputs/model_specs.csv` → `cars/inputs/model_specs.csv`,
  with `ModelSpec` and its loader out of `price_calculator.calculator` and
  `price_calculator.sources` into `cars/specs.py`.
* `docs/reference/` → `cars/reference/` — the grade comparisons and the Toyota
  PDFs behind them.
* `cars/inputs/trims.toml` and `cars/trims.py` are new: the グレード box on an
  auction sheet, in Japanese and in English.

## Why

The trim is what forced it. A Harrier's four trims are one AXUH80 with four
price tags about ¥1.5M apart, and the auction sheet's グレード box is the only
machine-readable thing that separates them. Reading it needs three facts that
were in three different places: which car the search named (`searches`), what
that model's lineup is (`docs/reference/harrier-grades.md`, prose only, read by
nothing), and how the report should say it (nowhere). There was no package the
new table could go in without one of the others reaching sideways into a
neighbour.

The model specs make the same point retrospectively. `price_calculator` holds
the argument about **money**, and ADR-0003 already separated the prices the
world sets this month from the arithmetic that is true for years. But a car's
length is neither: it is what Toyota built, it changes when a generation does,
and it was sitting in `price_calculator/inputs/` next to the RoRo rate. The
consequence was visible at the call sites — `banzai24.report` and
`dashboard.competitors` both imported `ModelSpecs` **from a pricing package** to
answer "how big is this car", and `price_calculator/__init__` advertised itself
as reading "the model specs" as though owning them.

So the two files that were `price_calculator/inputs/` were answering two
different questions:

| | changes when | wrong costs you |
|---|---|---|
| `costs.toml` | a supplier or the state changes a price | every number on the page |
| `model_specs.csv` | Toyota builds a new generation | one card's landed cost |

They are edited by different events and they fail differently. ADR-0003 says so
explicitly — a bad cost book raises and stops the run, a bad spec row degrades
to a reason on one card — and that asymmetry was the first sign these were not
the same kind of file.

## Alternatives

**Leave the specs where they are and put only the trims in `cars/`.** Rejected
because it splits one car across two packages on no principle anyone could
state: dimensions in the pricing package, trims in the car package, both keyed
by the same car. The next person adding a figure would have to guess, and the
guess is a coin flip.

**Put the trims in `searches/`.** Rejected for the reason ADR-0005 rejected
putting the URL slugs there: a search is a *declaration of what to buy*, and a
Harrier has four trims whether or not anyone is searching for one. It would also
have meant every search file could see a table only one of them uses.

**A `docs/`-only trims table, read by nothing.** This is what
`harrier-grades.md` already was, and it is why the trim never reached a report:
the file that knows a leather Z from a Z was prose, and the code that renders
the card had never heard of either. Keeping the reference prose next to the
machine-readable table — same package, `reference/` beside `inputs/` — is the
part of this change that is meant to stop that recurring.

**Keep `ModelSpec` in `calculator.py` and move only the CSV.** Rejected: the
loader would then live in a third place from both the data and the type, and
`cars` would have to import `price_calculator` to construct what it loaded,
which is the dependency this whole package exists to not have.

## Consequences

**`price_calculator` no longer owns a figure about a car.** `calculator.py`
holds arithmetic and imports `ModelSpec`; `sources.py` reads the cost book, the
rates and the two databases, and nothing else. The tests moved with the table:
`cars/tests/test_specs.py` is the CSV's, `price_calculator/tests/test_sources.py`
is the cost book's.

**A missing trim gloss is silent by design.** The match is an equality after the
4WD/hybrid modifiers are lifted out, so a box carrying a word nobody listed
prints its Japanese with no English and names `cars/inputs/trims.toml` on the
card. The alternative — containment — reads `Z レザーパッケージ` as a plain `Z`
unless carefully ordered, and that is €3,000 in the expensive direction with
nothing on the page to say a guess was made. `cars/trims.py` argues it at
length.

**Only the Harrier has a trims table.** The RAV4 has the prose and not the
table, and every other car has neither, so their cards print the sheet's
Japanese untranslated. That is the honest state and not a gap to paper over: the
Harrier's table was written from the sheets actually in this repo, and the next
car's should be too.

**Every extraction already in `auction.db` has a null `trim_ja`.** The column is
new, and re-reading a sheet costs money, so nothing was backfilled. The report
tells those apart from a genuinely blank グレード box by looking at the recorded
model output rather than at the column — see `LotView.trim` — because telling
the operator "the box was blank" about a sheet nobody ever looked at that box on
would be the page inventing a fact.
