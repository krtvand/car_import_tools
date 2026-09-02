# One search file, two parsers

> Amended by ADR-0008: `cars/` now sits one level below `searches/` and is the
> dependency root. Everything below still holds — `searches` still imports no
> parser, and no parser's vocabulary appears in a .toml.

`searches/` is the dependency root. One TOML file per car declares everything
about that car — the auction requirements, the bands and their max bids, who
counts as competition in Cyprus, and whether the dashboard shows it — and
`banzai24`, `bazaraki`, `price_calculator` and `dashboard` all read it.
`searches` imports none of them.

It validates only what it owns: the car, the `[[band]]` list, the competitor
bounds, `[dashboard]`. `[site]`, `[api]` and `[sheet]` pass through untouched as
raw tables, because their key names belong to banzai24's dataclasses and
checking them would mean importing the package.

**No parser vocabulary appears in a .toml.** A search names a car —
`car = "mazda-cx30"` — and each parser translates: `banzai24/cars.py` holds the
site's slugs (`MAZDA` / `CX-30`), `bazaraki/cars.py` holds the URL slugs, and
`cars/definitions.py` holds only the make and model as a person says
them — it was `searches/cars.py` when this was written, and moved out from
under `searches` for the reason ADR-0008 gives; nothing about the arrangement
changed with it.

## Why

Before this, the same car was declared in two places that could not see each
other: `banzai24/searches/mazda-cx30.toml` and `bazaraki/searches/mazda-cx30.sh`.
They had different bounds — the auction search wanted a 2023 car under 55,000 km,
the Cyprus crawl took 2022-and-newer between 10,000 and 70,000 — and nothing
could notice. There was no `mazda-3.sh` at all.

That is not merely untidy, because of how `bazaraki.db._in_scope` works:
delisting is bounded to the scope of the run that found the advert. An advert
outside every recent crawl keeps `is_active = True` for ever. When this was
written, **52 CX-30 adverts were flagged live and had not been inside a crawl's
scope for weeks** — they would have shown on the competitors panel as current
competition long after they sold. A panel whose scope and whose crawl are
declared separately produces false competitors and false silence, and silence
reads as good news.

One file makes crawl scope and panel scope the same object by construction.

## The slugs

`bazaraki` prefixes the make on some models: the RAV4 is at `/toyota/toyota-rav4/`,
and both `/toyota/rav4/` and `/toyota/rav-4/` 404. That was verified live. It is
not derivable, so it has to be written down — the only question was where.

Putting it in the .toml was rejected: the operator edits that file, and a URL
slug is not a fact about the car, it is a fact about a website. Putting it in
`searches` was rejected too — it would make the dependency root carry both
parsers' vocabularies, which is the coupling this whole arrangement exists to
avoid, only moved up a level. So each parser owns its own table, and the 404
evidence lives in the module that hit the 404.

The cost is real and accepted: `searches check` cannot tell you that bazaraki
has no slug for a car, because a pure `searches` cannot ask. That error surfaces
at `bazaraki scrape` and on the dashboard panel instead, naming the file to edit.

## Alternatives

**Deriving the slug with a hardcoded exceptions map** would put the RAV4 fact
back in one central place, at the price of a rule with exceptions — and the
exceptions are only discovered by 404ing in production.

**Keeping the two search systems and having the dashboard verify coverage**
against `ScrapeRun` would have caught the drift without merging anything. It was
rejected because it detects the problem rather than removing it: the two files
still drift, you are just told about it afterwards. The coverage check was kept
anyway — it is what catches a crawl that has not been re-run since the bounds
were widened.

## Consequences

**`bazaraki`'s `DEFAULT_FILTERS` is gone,** and with it `--no-defaults`, which
existed only to defend against it. `run_scrape` raises rather than falling back
to a default scope: a crawl with no scope would delist against the whole cars
category. `scrape --search` and the ad-hoc filter flags are mutually exclusive,
because a crawl whose scope no panel can reproduce is a crawl whose delisting
nobody can reason about.

**`--search` refuses a search with no `[band.competitors]` bounds** rather than
crawling every CX-5 ever listed in Cyprus. Wide is slow, but the real objection
is that the scope would match no panel.

**A wider crawl delists more.** The bounds most searches will want are wider than
the old `.sh` files', so the first completed run after this will mark a lot of
adverts gone — correctly. `--max-pages` now defaults to 10 rather than 3, because
a run truncated by that limit skips delisting entirely and would leave the
zombies exactly as they were.
