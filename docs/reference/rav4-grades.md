# RAV4 (2023): telling a HYBRID X from a HYBRID G

Why this file exists: the X is the bottom of the RAV4 range and it looks, in a
listing photo and in a Cyprus advert, almost exactly like the G. It sells for
noticeably less at both ends — new, ¥3,538,000 against ¥3,888,500; in Cyprus, a
2023 X with 39,000 km was asking €27,800 while a 2023 G with 47,000 km asked
€34,500 — so buying one by accident, or being priced against one, is expensive
in both directions.

Everything below is the Japanese lineup as of the 2022年10月 minor change, which
is what a 2023-registered car is. The 2023年10月 sheet repeats it unchanged:
same grades, same 型式, same drivetrains.

## The lineup

| | 2.0L petrol | 2.5L hybrid | 2.5L PHV |
|---|---|---|---|
| grades | Adventure · "Z package" · G · X | Adventure · G · X | Z |
| 型式 | MXAA54 (4WD) / MXAA52 (2WD) | AXAH54 (E-Four) / AXAH52 (2WD) | AXAP54 |

There is **no HYBRID Z**: the only Z in the range is the plug-in hybrid
(AXAP54), which is a different car at a different price and is outside every
`body_model_code` this search's bands name.

## The one rule that needs no judgement

**HYBRID G and HYBRID Adventure are E-Four only. Only the X has a 2WD version.**
The 諸元表 says so twice over: the G column reads `E-Four`, the X column reads
`E-Four / 2WD`, and the 車両型式 row leaves the 2WD cell empty for every grade
but X.

So **AXAH52 ⇒ HYBRID X**, with no photograph or sheet required — which is why
the code is a `[[band]]` key rather than an `[api]` one. The two codes are two
bands at two prices: the E-Four AXAH54 that this search is mainly about, and the
2WD AXAH52, which is an X and is worth less by exactly that much.

The full 型式 carries the grade too, when a document prints it in full:
`-ANXMB` = X, `-ANXGB` = G, `-ANXVB` = Adventure.

## Telling X from G on an AXAH54

From the official 主要装備一覧表. The left column is what you can see in a lot
photo or an advert photo, which is what makes it useful.

| | HYBRID X | HYBRID G |
|---|---|---|
| Wheels | 225/65R17 on 17×7J alloy, grey metallic — a visibly taller sidewall | 225/60R18 on 18×7J alloy, dark premium metallic |
| Front grille | black | gunmetal painted |
| Skid plate (front/rear) | none | silver painted |
| Front fog lamps | none (dealer-fit option only) | LED, standard |
| Door handles | body-coloured | body-coloured **with chrome moulding** |
| Backdoor garnish | body-coloured | body-coloured + bright silver |
| Roof glass | tilt & slide moonroof, ¥110,000 option | **panoramic** moonroof, ¥143,000 option |
| Screen | 8-inch display audio | **10.5-inch** display audio |
| Seats | fabric | synthetic leather, perforated |
| Driver's seat | 6-way manual | 8-way power, memory, power lumbar |
| Heated/ventilated seats | none | standard |
| Tailgate | manual | hands-free power tailgate |
| Door mirrors | plain | camera / blind-spot assist available |
| Digital rear-view mirror | not available | ¥66,000 option |
| nanoe X | none | standard |
| Boot side pocket | divider board | net |

The quickest three, in order: **the screen** (8" vs 10.5" is unmistakable in an
interior shot), **the seats** (fabric vs synthetic leather), **the wheels**
(17" with a fat tyre vs 18").

Two traps:

* the 18-inch wheel is a ¥49,500 factory option on the X, in the same design as
  the G's — so 18-inch wheels do not *prove* a G, they only fail to prove an X;
* there is no rear grade badge on either G or X (only Adventure gets one), so
  the tailgate tells you nothing.

## How the auction listing spells it

banzai24's Модификация field is the trim line as the auction house typed it, and
they type it four different ways for the same car:

```
5D 4WD HYBRID X · HYBRID X 4WD · X 4WD · X
5D 4WD HYBRID G · HYBRID G 4WD · G 4WD · 4WD HYBRID G
```

About one hybrid RAV4 in nine (6 of 54 distinct lots in the saved runs) says
only `4WD` or `HYBRID 4WD` — no grade at all.
That is why the search bans the word `X` (`[api] exclude_model_grades`) instead
of asking for `HYBRID G`: asking for the G's spelling would throw away every lot
listed as `G 4WD`, while banning `X` catches all four X spellings and leaves the
silent ones on the report, where the photographs answer the question.

## How a Cyprus advert spells it

bazaraki has no grade field, and roughly half the RAV4 adverts never mention a
grade. The ones that do put it in the free-text description, which is why the
scraper now stores it:

```
TOYOTA RAV 4 2.5L (G package) AWD hybrid …
Toyota RAV4 Hybrid X 2wd (Japanese Import) …
Toyota rav4 2.5x hybrid …
TOYOTA RAV4 2.5L HYBRID G-PACKAGE …
```

`exclude_phrases` drops the ones that say they are an X. An advert that says
nothing stays in the panel: it has not been shown to be an X, and it still takes
the sale.

It is written on the **AXAH54 band**, not under `[competitors]`, because the two
bands want opposite things from the same word. The AXAH54 is a G or an
Adventure, so an X advert undercuts it with a cheaper car. The AXAH52 *is* an X,
so those adverts are the market it sells into and dropping them would leave that
band's panel measuring nothing. A phrase under `[competitors]` applies to every
band; a band's own phrases add to it.

## Sources

The originals are in `docs/reference/rav4/`. Toyota removed them from toyota.jp
when the new RAV4 launched, so the links are Wayback captures.

* [RAV4 主要装備一覧表 2022年10月](https://web.archive.org/web/2024/https://toyota.jp/pages/contents/rav4/002_p_001/4.0/pdf/spec/rav4_equipment_list_202210.pdf)
  — the grade-by-grade equipment table every row above comes from.
* [RAV4 主要諸元表 2022年10月](https://web.archive.org/web/2024/https://toyota.jp/pages/contents/rav4/002_p_001/4.0/pdf/spec/rav4_spec_202210.pdf)
  — 型式, drivetrain per grade, weights, dimensions.
* [RAV4 主要諸元表 2023年10月](https://web.archive.org/web/2024/https://toyota.jp/pages/contents/rav4/002_p_001/4.0/pdf/spec/rav4_spec_202310.pdf)
  — the later 2023 sheet, unchanged in everything used here. (Its equipment-list
  companion is indexed by the Wayback Machine but does not come back; the
  2022年10月 one stands in for it.)

New prices are from the model catalogue pages for
[6AA-AXAH52 ハイブリッドX](https://www.nextage.jp/carcatalog/toyota/rav4/6aa-axah52/10145621/)
and [6AA-AXAH54 ハイブリッドG](https://www.nextage.jp/carcatalog/toyota/rav4/6aa-axah54/10124195/),
not from Toyota, and are there for scale rather than as a price reference.
