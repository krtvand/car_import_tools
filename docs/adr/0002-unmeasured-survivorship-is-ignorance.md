# An unmeasured survivorship factor is ignorance, not zero discount

`bazaraki.analysis.sale_adjustment_factor` combines two signals into the
asking→sale multiplier, and falls back to `DEFAULT_SALE_ADJUSTMENT = 0.92` when
neither is usable. **A survivorship factor at or above 1.0 no longer counts as a
usable signal.** It is discarded, and the default stands.

## Why

The survivorship signal is `exp(median_resid_fast − median_resid_linger)`: below
1 when cars that sold quickly were priced under what is still on the market. The
result is then clamped to `[0.5, 1.0]`, which encodes the belief that this
factor can only ever *be* a discount.

A value above 1 is that belief being contradicted by the data. The old code
responded by truncating it to exactly 1.0, marking the signal `used`, and
thereby suppressing the default entirely — so "fast sellers were not cheaper"
was recorded as the measurement "there is no asking-to-sale gap".

This was not a thin-data problem, which is why no sample-size threshold fixes
it. The CX-5 had **14 fast sales against 82 lingering adverts** — ample by
`MIN_GROUP = 3` — and produced 1.0059. Under the old rule every Mazda's
`sale_price` came back **exactly equal to its `asking_estimate`**, and the 0.92
default never fired for any car in `bazaraki.db`.

Alternatives considered: a deadband (`< 0.98`), which needed a threshold nobody
could justify; and a Mann-Whitney test on the two residual groups, which is
correct but is machinery bought for a signal that is provisional anyway
(`PRICING_PLAN.md` Part D calibrates it once weeks of delisting data accrue).
The comparison against 1.0 is the same claim the clamp already makes, applied
one step earlier.

## Consequences

**Every Mazda's resale estimate drops about 8% the day this lands**, and with it
every margin computed from one. On the CX-5 at ¥2,055,000 that is roughly €2,000
of apparent profit that was never there. The RAV4 moves less: its price-cut
signal (n=7) was already firing, holding it at 0.984.

The visible change is large and its cause is not visible in a diff — it is one
`and` clause. A future reader benchmarking the two versions will see every Mazda
move together with no obvious reason, and the obvious "fix" is to revert this,
which restores the bug.

`sale_price` and `asking_estimate` are now *usually different numbers*, where
before they were usually identical. Any code that treated them as
interchangeable was relying on the bug.

The 0.92 that now stands is **assumed, not measured**. That is the honest state
of the world today, and it is why `adjustment_factor` is printed next to every
Cyprus estimate the price calculator produces: `×0.920` is a statement that
there is no sale data for this car, and it should read as one.
