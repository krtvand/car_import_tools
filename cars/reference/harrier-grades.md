# Harrier (2023): telling S, G, Z and Z "Leather Package" apart

Why this file exists: the Harrier's four trims are one car with four price tags,
and — unlike the RAV4 — **nothing in the chassis code says which one you are
looking at**. AXUH80 is every 2WD hybrid Harrier from the cheapest to the
dearest. The trim line the auction house typed is the only machine-readable
answer there is, which is why there are four search files rather than one with
four bands, and why the word "LEATHER" is load-bearing in two of them.

It matters by about ¥1.5M. In one archive walk of 2023 hybrids under 50,000 km,
a G hammered ¥2,399,000 and leather Zs hammered ¥3,347,000–¥3,857,000. In
Cyprus the same four trims are advertised between €26,500 and €33,500 — a much
narrower spread, which is the whole commercial point of this file.

Everything below is the Japanese lineup as of the **2023年10月** 主要諸元表 and
主要装備一覧表, which is what a 2023-registered car is.

## The lineup

| | 2.0L petrol | 2.5L hybrid | 2.5L PHEV |
|---|---|---|---|
| grades | Z "Leather Package" · Z · G · S | Z "Leather Package" · Z · G | Z |
| 型式 | MXUA80 (2WD) / MXUA85 (4WD) | AXUH80 (2WD) / AXUH85 (E-Four) | AXUP85 |

Two things follow, and both are written into the search files:

* **The S is a petrol grade.** The 2023年10月 sheet lists it only in the
  ガソリン車(2WD) column, on MXUA80 alone — see "The hybrid S" below, because the
  auctions disagree.
* **The PHEV is a different car**, AXUP85, outside every `body_model_code` the
  four searches name, and on bazaraki it is a different fuel type
  ("Plug-In Hybrid Petrol") rather than a dearer Harrier. It is fenced off at
  both ends and never needs a trim rule.

The full 型式 carries the grade in its suffix, when a document prints it out:
`-ANXSB(S)` = Z "Leather Package", `-ANXSB` = Z, `-ANXGB` = G, `-ANXMB` = S.
Auction listings print the short code and never the suffix, so this is a way to
read a registration document, not a way to filter lots.

## The rule the RAV4 has and this car does not

On the RAV4, `AXAH52 ⇒ HYBRID X` with no photograph required, because Toyota
only ever built the cheap grade in 2WD. **The Harrier has no such shortcut.**
The 諸元表 gives every hybrid grade both a 2WD and an E-Four 型式:

| | Z "Leather Package" | Z | G |
|---|---|---|---|
| 2WD | 6AA-AXUH80-ANXSB(S) | 6AA-AXUH80-ANXSB | 6AA-AXUH80-ANXGB |
| E-Four | 6AA-AXUH85-ANXSB(S) | 6AA-AXUH85-ANXSB | 6AA-AXUH85-ANXGB |

So the code says the drivetrain and stops. A `[[band]]`, which prices a year, a
mileage range and a list of codes, therefore cannot separate a G from a leather
Z — and that is the whole reason this car is four files instead of one.

The E-Four is worth more in Japan: in the archive walk the leather Zs went
¥3,496,000–¥3,857,000 on AXUH85 against ¥3,347,000–¥3,403,000 on AXUH80. All
four files nonetheless price both codes in one band, because there is no Cyprus
data yet saying the E-Four sells for the difference. Split it when the panel can
answer that.

## The hybrid S, which Toyota does not list

The 2023年10月 sheet has no hybrid S at all — it was in the 2020年6月 lineup
(along with a G "Leather Package" that also no longer exists) and did not
survive the change. The auctions carry it anyway: **15 lots in the walk, badged
`HYBRID S`, on AXUH80, registered 2023.07 and 2023.08.**

They are not a catalogue mistake and they are not scattered. They arrive in
consecutive lot numbers at one TAA Kanto sale — 52022, 52030, 52034, 52036,
52037, 52038, 52040, 52041 — at 37,000–58,000 km in about three years, nearly
all opening at exactly ¥1,000,000. That is a fleet disposal, and the auction
grades say so too: almost all are 3 or 3.5, which `[site] grade` already refuses.

Two consequences for `toyota-harrier-s.toml`:

* expect it to return very little, because the grade filter removes most of the
  population before the trim rule is even reached;
* read the 車歴 on anything it does return before trusting a `private` max bid.
  A batch like this is the case the `rental` price in `max_bid_jpy` exists for.

## Telling them apart in a photograph

From the 主要装備一覧表 2023年10月 in `cars/reference/harrier/`. Rows chosen for
being visible in a lot photo or a Cyprus advert photo, which is what makes them
useful; ● is standard, ○ a factory option, — not available.

| | S | G | Z | Z "Leather Package" |
|---|---|---|---|---|
| **Wheels** | 225/65R17 on 17×7J, grey metallic | 225/60R18 on 18×7J, machined + dark grey | **225/55R19** on 19×7J, super chrome metallic | **225/55R19**, same as Z |
| **Screen** | 8-inch, 6 speakers | 8-inch, 6 speakers | **12.3-inch HD, 9 speakers (JBL)** | **12.3-inch HD, JBL** |
| **Seats** | fabric | fabric + synthetic leather | fabric + synthetic leather | **genuine leather** |
| Passenger seat | 4-way manual | 4-way manual | **4-way power** | **4-way power** |
| Driver's seat | 6-way manual | 8-way power | 8-way power | 8-way power |
| Lumbar support | — | 2-way | **4-way** | **4-way** |
| Heated + ventilated front seats | — | — | ● | ● |
| Seat/steering position memory | — | — | ● | ● |
| Power tailgate | — | — | ● hands-free | ● hands-free |
| Panoramic roof (dimming, powered shade) | — | — | ○ ¥198,000 | ○ ¥198,000 |
| Rear roof spoiler | black | black | **body-coloured** | **body-coloured** |
| Door handles | body-coloured | chrome | chrome | chrome |
| Headlamps | 3-lamp LED | projector LED + LED DRL | projector LED + LED DRL | projector LED + LED DRL |
| LED front fog lamps | dealer option | ● | ● | ● |
| Digital rear-view mirror | ○ ¥88,000 | ● | ● | ● |
| Blind spot monitor | ○ ¥68,200 | ● | ● | ● |
| Panoramic view monitor | — | — | ○ ¥88,000 | ○ ¥88,000 |
| Front scuff plates | plain | stainless, car-name logo | **stainless, illuminated** | **stainless, illuminated** |
| Heater control panel | dial | dial (touch ○) | capacitive touch | capacitive touch |

The quickest reads, in order:

1. **The screen.** 8-inch against 12.3-inch is unmistakable in any interior
   shot, and it is the single line that splits G from Z.
2. **The seats.** Genuine leather is the *only* thing that makes a leather Z a
   leather Z — a plain Z is fabric with synthetic leather, exactly like a G. If
   the seats are cloth-centred, it is not a leather package, whatever the
   advert says about "leather seats".
3. **The wheels.** 19-inch on both Zs, 18-inch on a G, 17-inch with an obvious
   sidewall on an S.
4. **The spoiler**, from behind: body-coloured on a Z, black on a G or S.

The trap worth knowing: the Z and the Z "Leather Package" are visually the same
car from outside — same 19-inch wheels, same body-coloured spoiler, same
lights. Only the interior separates them, and only the seat covering does it
reliably.

## How the auction listing spells it

banzai24's Модификация field is the trim line as the auction house typed it.
Every distinct 2023 hybrid lot (AXUH80/AXUH85) in one archive-plus-auctions
walk, 70 lots:

```
HYBRID Z LEATHER PACKAGE (17) · HYBRID Z LEATHER PACKAGE 4WD (4) · 4WD HYBRID Z LEATHER PACKAGE (2)
HYBRID Z (17) · HYBRID Z 4WD (1) · 4WD HYBRID Z (1)
HYBRID S (15)
HYBRID G (11) · 4WD HYBRID G (1)
(nothing at all) (1)
```

Better behaved than the RAV4, where about one hybrid in nine names no grade:
here it is one in seventy. The grade word is always spelled the same; only the
`4WD` moves around it, which is why `exclude_model_grades` — matched as whole
consecutive **words**, so word order does not matter — catches every spelling.

Three of the four searches are written as exclusions, for the reason the RAV4's
are: a lot naming no grade is *kept*, and reaches the report where the
photographs can answer. The cost is that the one silent lot reaches the S, the G
and the Z reports alike.

**The leather Z is the exception, and the reason `model_grades` exists.** Its
trim line *contains* a plain Z's, so no set of banned words keeps it without
also keeping the cheaper car; the grade has to be asked for rather than banned.
`toyota-harrier-z-leather.toml` therefore says `model_grades = ["LEATHER"]` —
one word, because it appears in all three spellings and cannot mean any other
2023 Harrier. Asking rather than banning flips what silence means: a lot with a
blank trim line is dropped there, where the other three keep it.

banzai24's own `modelGrade` query parameter is **not** used by any of these
files. It changed nothing on any value tried against the live search, so the
trim split is done entirely in `[api]`, in code this repo owns and tests.

## How a Cyprus advert spells it

bazaraki has no grade field. Of 33 adverts for 2.5 hybrid Harriers, **11 name a
grade** and they do it in the free text, in six different ways:

```
Toyota Harrier Hybrid G Model (Japanese Import) … G package
Toyota Harrier Hybrid 09/2022 G Package New Japan Import
TOYOTA HARRIER HYBRID G — 2023 … G PACKAGE
Toyota Harrier Hybrid S (Japanese Import) … S package
TOYOTA HARRIER Z PACKAGE 2023
TOYOTA HARRIER 'Z' HYBRID … "Z: edition , leather package
TOYOTA HARRIER Z LEATHER PACKAGE 2024 · -Z Leather Package -Japan import
Toyota Harrier MODELISTA (Lexus RX 350h) … Z package top of range
```

`exclude_phrases` on each band drops the trims *below* the one being sold, and
only those: an advert that never names a grade stays in the panel, because it
has not been shown to be a cheaper car and it still takes the sale. So the G's
band excludes `hybrid s` / `s package`, and the two Z bands exclude the G's
spellings as well.

Two Cyprus-side traps:

* **"leather" on its own means nothing here.** Cyprus adverts list "Leather
  Seats" as a feature on cars that are plainly not leather-package Zs. Only the
  full phrase `leather package` is evidence, which is why the exclusions never
  ban the bare word the way the auction-side filter does.
* **The other two Harriers fence themselves off.** The 2.0 petrol is dropped by
  `engine_size 2.5–2.5`, and the PHEV — the one advert asking €41,900, which
  would lift any panel it landed in — is dropped by `fuel_type = ["hybrid
  petrol"]`, because bazaraki files it under "Plug-In Hybrid Petrol".

## What each one is actually worth

Japanese hammer prices are completed sales from the archive walk, 2023 cars
under 50,000 km. Cyprus figures are asking prices; the operation's own estimate
knocks about 8% off them for resale. Landed is this repo's calculator at
¥185.22/€.

| | Japan, hammer | landed | Cyprus, asking |
|---|---|---|---|
| S | no completed sale seen; fleet lots **open** at ¥1,000,000 (37–58k km) | €10,786 | €26,500 (22,000 km) |
| G | ¥2,399,000 (11,000 km), n=1 | €20,309 | €28,000 (29,000 km) · €29,500 (22,000 km) |
| Z | ¥2,404,000–¥3,450,000 (9–21k km), n=4 | €20,341–€27,323 | €29,900 (32,750 km) |
| Z "Leather Package" | ¥3,347,000–¥3,857,000 (20–40k km), n=5 | €26,649–€29,989 | no 2023 advert; 2024s ask €32,400 and €33,500 |

Read down the table and the shape of the trade is plain: **Japan charges a great
deal for the top trims and Cyprus does not pay it back.** The dearest leather Z
lands at €29,989 against a €32,400 asking price for a car a year newer — no
margin at all — while the cheap end has room to spare. The bids in the four
files are solved from the Cyprus side at the 20% target, so `harrier-z` and
`harrier-z-leather` will very likely show lots priced over their ceiling for
weeks at a time. That is the measurement, not a fault in the ceiling.

Everything in that table is thin — one G sale, one Z advert, no 2023 leather Z
advert at all, and no Harrier in `bazaraki.db` until these searches have run
once. Re-derive the bids with `python -m price_calculator` after the first
scrape, when the Cyprus column is a fitted curve rather than four adverts.

## Sources

The PDFs are in `cars/reference/harrier/`, fetched from toyota.jp. The 2022年10月
sheets — what a 2023 car strictly is — are no longer served; the 2023年10月 pair
is the same facelift lineup and stands in for them, and the 2020年6月 spec is
kept because it is the one that still lists the hybrid S.

* [ハリアー 主要装備一覧表 2023年10月](https://toyota.jp/pages/contents/harrier/004_p_001/4.0/pdf/spec/harrier_equipment_list_202310.pdf)
  — every equipment row above.
* [ハリアー 主要諸元表 2023年10月](https://toyota.jp/pages/contents/harrier/004_p_001/4.0/pdf/spec/harrier_spec_202310.pdf)
  — 型式 per grade and drivetrain, dimensions, WLTC economy. The
  4,740×1,855×1,660 mm and the 22.3 km/L behind the `model_specs.csv` row are
  from here.
* [ハリアー 主要諸元表 2020年6月](https://toyota.jp/pages/contents/harrier/004_p_001/4.0/pdf/spec/harrier_spec_202006.pdf)
  — the pre-facelift lineup, with the hybrid S and the G "Leather Package" that
  the 2023 sheet no longer has.
* [ハリアー 主要装備一覧表 2024年1月](https://toyota.jp/pages/contents/harrier/004_p_001/4.0/pdf/spec/harrier_equipment_list_202401.pdf)
  — kept as a cross-check; unchanged in every row used here.

Auction and Cyprus figures are from a live walk of banzai24 and bazaraki on
2026-08-31, not from a saved run.
