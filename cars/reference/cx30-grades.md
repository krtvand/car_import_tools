# CX-30 (2023): reading the グレード box

Why this file exists: `cars/inputs/trims.toml` points here, and every English
name in the `[mazda-cx30]` table has to come from somewhere. Unlike
`harrier-grades.md` and `rav4-grades.md`, **that somewhere is not a catalogue**
— nobody has read a 主要装備一覧表 for this car and there is no PDF under
`cars/reference/cx30/`. Everything below is measured off **88 distinct CX-30
lots**, from `runs/*/lots.json` *and* `auction.db`, and the file says which of
them each claim rests on.

Both sources are needed, and the run files are the half that is easy to miss:
five of the 88 were fetched and never normalised into the database. They add no
trim the table did not already have here — but the same query against MAZDA3
missed an entire engine family, which is why `mazda3-grades.md` says so at
length.

Read that as a limit, not a disclaimer. It is enough to gloss a sheet, which is
what the table is for. It is *not* enough to tell two trims apart in a
photograph the way the Harrier file can, and the `note` lines are correspondingly
thin.

## How Mazda names a grade

An engine designation, then a suffix:

| designation | engine | in this data |
|---|---|---|
| `20S` | 2.0 petrol | every lot but one |
| `X` | SKYACTIV-X 2.0 | one lot (`X BLACK TONE ED`) |
| `XD` | 1.8 diesel | none — `engine_capacity_start = 1.9` never fetches it |

So the engine is **part of the trim**, not a modifier: a `20S` and an `X` are
different money, and `cars/trims.py` keeps the designation inside the `ja`
spelling rather than lifting it out the way it lifts `4WD`.

## Every box a sheet has actually printed

Five, across five lots. All five match the table.

| box, verbatim | lots | what the list API said about the same car |
|---|---|---|
| `20S プロアクティブ ツーリングセレクション` | `39-1580-21184`, `39-1580-27452`, `39-1580-55648` (U Tokyo) | `20S PROACTIVE TOURING`, twice with `4WD` in front |
| `20S ブラックトーンエディション` | `39-1580-55479` (U Tokyo) | `4WD 20S BLACK TONE` |
| `ブラックトーンエディション 20S` | `6-2057-2010` (JU Fukushima) | **`4WD 20S`** — the listing never said Black Tone |
| `20S ﾌﾟﾛｱｸﾃｨﾌﾞ ﾂｰﾘﾝｸﾞｾﾚｸｼｮﾝ` | the test fixture sheet | — |

Two things in that table are the reason the `ja` lists look the way they do.

**The word order moves.** U Tokyo types `20S ブラックトーンエディション`, JU
Fukushima types `ブラックトーンエディション 20S`. The fold drops spaces but never
reorders words, and the match is equality, so both spellings have to be listed
or one house's cars come back unglossed.

**The half-width box.** The fixture sheet's `ﾌﾟﾛｱｸﾃｨﾌﾞ ﾂｰﾘﾝｸﾞｾﾚｸｼｮﾝ` is the case
NFKC folding exists for; `cars/tests/test_report.py` pins it.

And `6-2057-2010` is the whole argument for reading the sheet at all: the
listing called it `4WD 20S`, the sheet called it a Black Tone Edition. The
listing's silence about a trim is not evidence there isn't one —
`docs/adr/0009-api-grades-are-rejudged-at-render.md` is about the report saying
so out loud.

## How the auction listing spells it

All 88 CX-30 lots, from the Модификация field:

```
20S PROACTIVE TOURING (32) · 4WD 20S PROACTIVE TOURING (6) · 20S PROACTIVE TOURING SET (7)
  · 4WD20S PROACTIVE TOURING (1) · 20S PROACTIVE TOURING 2WD (1)
20S (18) · 20S 4WD (4) · 4WD 20S (1)
20S PROACTIVE (5) · 20S PROACTIVE 2WD (1)
20S BLACK TONE (4) · 20S BLACK TONE ED (3) · 20S BLACK TONE ED 4WD (1) · 4WD 20S BLACK TONE (2)
20S RETRO SPORTS (1)
X BLACK TONE ED (1)
```

Better behaved than either Toyota in one respect — **every lot names a grade**,
where about one hybrid RAV4 in nine names none. Worse in another: the list
truncates. `20S PROACTIVE TOURING` and `20S PROACTIVE TOURING SET` are both
*PROACTIVE Touring Selection*, and Mazda sells nothing called "20S PROACTIVE
Touring". Any `[api] model_grades` written for this car has to match on a word
that survives the truncation.

## The price order

Median concluded price, from the 32 CX-30 lots that have one. This is what
`rank` in `trims.toml` is ordered by — measured, not catalogued.

| trim | median | n |
|---|---|---|
| 20S | ¥1,712,500 | 16 |
| 20S PROACTIVE TOURING | ¥1,817,500 | 8 |
| 20S PROACTIVE | ¥1,875,000 | 1 |
| 20S BLACK TONE ED | ¥1,875,000 | 1 |
| 20S PROACTIVE TOURING SET | ¥1,920,000 | 5 |
| 20S BLACK TONE | ¥2,085,000 | 1 |

The spread from base to Black Tone is about ¥370,000 — real money against a
¥1,733,500 max bid, and the reason glossing the box is worth doing. Treat every
row with `n=1` as a single sale rather than a price.

## What is deliberately not in the table

**L Package, Smart Edition, and the whole `XD` diesel family.** No CX-30 lot has
printed any of them. Adding them would mean transcribing a catalogue nobody here
has read, and `trims.toml` would stop being a record of what was seen. A lot
printing one comes back unmatched, shows its Japanese on the card, and the card
names the file to edit — which is the designed signal, and a line to add with a
lot number beside it.

## What is not known

**The chassis code does not tell you the drivetrain**, at least not in this
data. Against the sheets' own drivetrain box:

| code | 2WD | 4WD |
|---|---|---|
| `DMEJ3P` | 16 | 7 |
| `DMEJ3R` | 3 | 1 |

Both codes carry both, so neither can stand in for the other — which is why
`mazda-cx30.toml` asks for `[sheet] drivetrain = "2WD"` rather than splitting
bands on the code the way `toyota-rav4-x.toml` does. Whether that is Mazda's
scheme or banzai24 recording the code loosely is unresolved; a 諸元表 would
settle it.

**No equipment list.** Nothing here can tell a PROACTIVE from a PROACTIVE
Touring Selection in a lot photo. If that starts mattering — it is a ¥100,000
question on the medians above — the fix is the Harrier's: put the 主要装備一覧表
in `cars/reference/cx30/` and write the comparison table from it.
