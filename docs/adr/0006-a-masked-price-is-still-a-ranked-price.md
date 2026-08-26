# A masked price is still a ranked price

banzai24 hides the hammer price on lots it sells through its own channel, and
shows one per click. **`banzai24 stats` trusts the site's ascending price sort to
rank those masked lots correctly, and reveals a price only for a lot it is going
to print.** It does not reveal the window in order to sort it itself.

## Why

`sortPriceEnd=asc` returns masked lots interleaved among visible ones rather
than bunched at either end, which is only possible if the server is ordering on
a price it declines to show. Measured on 2026-08-26 against the RAV4 archive
(2023, ≤50,000 km, `HYBRID G`, grades 4/4.5/5, `status=SOLD&source=archive`) by
revealing all twenty lots on the first ascending page in row order:

```
returned:  [None, 2960000, None, None, None, None, 3117000, 3135000, ...]
revealed:  [2885000, 2960000, 2970000, 3040000, 3065000, 3105000, 3117000, ...]
```

Twenty values, strictly ascending, no gaps, no zeros. The masking is a display
concern; the ordering is real.

That makes the cheap end of a window reachable without reading the window. Page
one *is* the twenty cheapest lots, so the walk inspects from the top and stops
at five keepers — and the expensive half of this feature is the sheet
extraction, not the fetch.

Two alternatives, both rejected:

**Reveal every masked lot, then sort locally.** 54 of 99 lots in that archive are
masked, so this is ~54 clicks per band per run to learn an ordering the server
already gave away. It is the only option that verifies the ranking rather than
trusting it, which is the entire case for it.

**Skip masked lots and rank the 45 that show a price.** Cheap, and wrong. The
masked lots are not a random 54%: every one carries `Только на BANZAI24` and
none of the others do — they are one sales channel, which may price unlike the
open ring. Choosing "the cheapest acceptable sales" from the visible channel
only would be a measurement of that channel presented as a measurement of the
market, which is what `0002-unmeasured-survivorship-is-ignorance.md` refuses.

## Consequences

**The correctness of the page rests on someone else's ORDER BY.** If banzai24
ever sorts masked lots as zero, or last, or by `updatedAt` under a renamed
parameter, the walk inspects the wrong twenty lots and the page still renders —
five plausible cars that are not the cheapest acceptable ones. Nothing about the
output would look wrong.

So the ordering is asserted rather than assumed: a revealed price below the
previous keeper's means the ranking has broken, and the run says so instead of
finishing. It is a cheap check because the run reveals prices in walk order
anyway.

**A revealed price is read through a real button click.** The reveal endpoint,
`GET /api/catalog-service/lots/get-price/<lot-uuid>`, answers **HTTP 200 with
`priceStart: 0, priceEnd: 0`** to any caller lacking the Authorization header
the SPA adds from memory — verified for both an in-page `fetch()` and `httpx`
with the saved cookies. It does not answer 401. A zero is therefore a failed
read and never a price, and the reveal stays inside the arrangement `fetch.py`
already describes: let the page make its own authenticated calls and intercept
the responses.

**Fewer lots are inspected than are ranked, on purpose.** The run knows the
price order of every lot in the window and the price of at most five. That is
enough to print the page and not enough to say anything about the distribution —
which is why the histogram that was specified first is not in this feature, and
would need a different fetch if it comes back.
