# Imported car price calculator — spec

Computes the **expected landed price in EUR** of a car bought at a Japanese
auction and imported to Cyprus.

> **The values in this document are the spreadsheet as it stood at the port
> (January 2026). They are not the prices anything runs on.** Today's prices are
> `price_calculator/inputs/costs.toml` — the **cost book** — and that file is
> the only place they are edited. This spec keeps the *formulas*, the cell
> references and §6's worked example, because those are what you want when
> reconciling the code against the sheet and they do not go stale. Its value
> tables were removed after they spent a month disagreeing with both the sheet
> and the code; see `docs/adr/0003-prices-are-data-not-code.md`.

Source of truth: Google Sheet
[Imported car price calculator public](https://docs.google.com/spreadsheets/d/19adWxKYk-NljDLaB_CVQ5IQ_BnJna_vpIDgRMaNIKv4/edit?gid=1620773452),
sheets `Calculator` and `Exporter service fees`. Cell references below (`B4`,
`C10`, …) point back to that sheet so the two can be kept in sync.

---

## 1. Inputs

| Name | Unit | Sheet cell | Notes |
|---|---|---|---|
| `auction_price` | JPY | `Calculator!B4` | Hammer price of the lot |
| `length_cm` | cm | `Calculator!B38` | Body length |
| `width_cm` | cm | `Calculator!B39` | Body width |
| `height_cm` | cm | `Calculator!B40` | Body height |
| `usd_jpy` | JPY per USD | `Calculator!B33` | Live: `GOOGLEFINANCE("CURRENCY:USDJPY")` |
| `eur_jpy_market` | JPY per EUR | `Calculator!B32` | Live: `GOOGLEFINANCE("CURRENCY:EURJPY")` |

Dimensions are the only car params that affect the price beyond the auction
price: they drive the freight cost via shipping volume.

Everything else the calculation needs — every fee, rate, tax and bill, including
`road_tax_eur` — arrives as one `CostBook`, loaded from `costs.toml`. The sheet
treats road tax as a per-car input because it is a function of CO₂ (§5); the
code carries it on the book because it is flat today, and `dataclasses.replace`
is how one car is priced with a different figure.

---

## 2. Rates and constants

**Values live in `costs.toml`, not here.** This section maps each price to the
cell it came from and to the key that now holds it, so the sheet and the book
can be reconciled line by line. To see what a number *is*, open the book.

### Exchange rates

```
eur_jpy_effective = eur_jpy_market - bank.eur_jpy_spread   # Calculator!B32
```

`usd_jpy` is used as-is. The haircut on EUR/JPY is a deliberate conservative
margin on the conversion actually obtained by the bank, and USD/JPY has no
equivalent.

### Tax rates (`Calculator!B34:B35`)

| Sheet cell | Cost book key |
|---|---|
| `B34` VAT (Cyprus) | `taxes.vat_rate` |
| `B35` Import duty | `taxes.duty_rate` |

> Note on `Calculator!B35` (Duty): *"Starting January 2026, import duty on cars
> originating from Japan is reduced to zero, provided that all required
> documentation is submitted."* The zero is a condition being met, not a
> property of the world — which is why it is a line in the book rather than a
> constant in the code.

### Exporter fees (`Exporter service fees` sheet)

A tiered service fee by auction price (`A3:C6`), looked up with an
approximate/sorted `VLOOKUP` on `auction_price`. In the book it is
`[[exporter.service_fee]]`, one table per band, ascending by `up_to_jpy`; bands
are **inclusive of their upper bound**.

| Sheet cell | Cost book key |
|---|---|
| `C8` Exporter fixed fee | `exporter.fixed_fee_jpy` |
| `C9` Certificate of origin | `exporter.certificate_of_origin_jpy` |
| `C10` RoRo shipping, per m³ | `freight.roro_per_m3_usd` |
| `B19` Freight insurance, flat | `freight.insurance_usd` |

Behaviour outside the table: a price above the last band falls back to the last
tier under sorted `VLOOKUP`, and the code does the same while setting
`above_fee_table`, because the sheet's silence there is an accident of `VLOOKUP`
rather than a quoted price. Below ¥1 the lookup fails; `auction_price <= 0`
raises.

### Fixed expenses in Cyprus, EUR (`Calculator!B21:B29`)

| Item | Sheet cell | Cost book key |
|---|---|---|
| SVA test | `B22` | `cyprus.sva_test_eur` |
| MOT | `B23` | `cyprus.mot_eur` |
| Registration in Department of Road Transport | `B24` | `cyprus.registration_eur` |
| Delivery order and other customs clearance expenses | `B25` | `cyprus.customs_clearance_eur` |
| Number plates | `B26` | `cyprus.number_plates_eur` |
| Car service (oil, filters) | `B27` | `cyprus.car_service_eur` |
| Road tax | `B28` | `cyprus.road_tax_eur` *(a function of CO₂ — see §5)* |
| Insurance | `B29` | `cyprus.insurance_eur` |
| **Total** | `B21` | `CostBook.fixed_expenses_base_eur` + road tax |

`fixed_expenses_base_eur` is the sum of the seven that never vary; road tax is
added separately because it is the one line that depends on the car.

### Bank transfer fees (`Calculator!B10`)

```
bank_transfer_fees_eur = cnf_price_eur * bank.fx_rate + bank.international_transfer_eur
```

- `bank.fx_rate` — Revolut FX/exchange fee
- `bank.international_transfer_eur` — two international payments × €30

---

## 3. Calculation

All intermediate money is JPY until the CNF conversion, then EUR.

### 3.1 CNF price (JPY) — `Calculator!B15`

```
exporter_fees_jpy = tier_fee(auction_price)              # exporter.service_fee
                  + exporter.fixed_fee_jpy
                  + exporter.certificate_of_origin_jpy

volume_m3   = (length_cm * width_cm * height_cm) / 1_000_000

freight_jpy = freight.roro_per_m3_usd * usd_jpy * volume_m3

insurance_jpy = freight.insurance_usd * usd_jpy          # 0 ships uninsured

cnf_price_jpy = auction_price
              + exporter_fees_jpy
              + freight_jpy
              + insurance_jpy
```

`freight_jpy` mirrors the sheet's custom Apps Script function
`FreightCost(header, USD_JPY, unit_price_usd, volume_cm)`:

```js
function FreightCost(header, USD_JPY, unit_price_usd, volume_cm) {
  if (!header.includes('reach out on Telegram https://t.me/AndreyKartaev')) {
    throw new Error('invalid form');
  }
  var unit_price_jpy = unit_price_usd * USD_JPY;
  var volume_m = volume_cm / 1000000;
  return unit_price_jpy * volume_m;
}
```

(The `header` check is a watermark guard in the shared sheet and has no place
in the port.)

### 3.2 Convert to EUR — `Calculator!B7`

```
cnf_price_eur = cnf_price_jpy / eur_jpy_effective
```

`cnf_price_eur` is the customs value on which duty and VAT are charged.

### 3.3 Costs payable in Cyprus (EUR) — `Calculator!B9`

```
bank_transfer_fees = cnf_price_eur * bank.fx_rate                    # B10
                   + bank.international_transfer_eur
duty               = cnf_price_eur * taxes.duty_rate                 # B11
vat                = (cnf_price_eur + duty) * taxes.vat_rate         # B12
fixed_expenses     = fixed_expenses_base_eur + cyprus.road_tax_eur   # B13 -> B21

to_pay_in_cyprus = bank_transfer_fees + duty + vat + fixed_expenses
```

Note VAT is charged on **CNF + duty**, not on the bank fees or the local fixed
expenses.

### 3.4 Total — `Calculator!B5`

```
total_eur = cnf_price_eur + to_pay_in_cyprus
```

---

## 4. Outputs

The module should return the breakdown, not just the total — the sheet's value
is in showing where the money goes:

```
total_eur
cnf_price_eur
to_pay_in_cyprus_eur
  bank_transfer_fees_eur
  duty_eur
  vat_eur
  fixed_expenses_eur (itemised)
cnf_price_jpy
  auction_price_jpy
  exporter_fees_jpy
  freight_jpy
  freight_insurance_jpy
rates_used: { usd_jpy, eur_jpy_market }
costs_used: the whole CostBook, stamped onto the answer
```

Both are carried on the result, not looked up inside it: a landed cost is a
statement about a moment, and re-rendering it must not reprice it.

---

## 5. Road tax

Road tax is the one fixed expense that varies per car; it is a function of CO₂
emissions (g/km). From the sheet comment on `Calculator!A28`:

- Calculator: <https://cyprusgloballogistics.com/cyprus-road-tax-caclulator/>
- CO₂ figures for a given model can be looked up in the drom.ru catalog,
  e.g. <https://www.drom.ru/catalog/mazda/cx-60/431143/>

Until CO₂ per model is available, `cyprus.road_tax_eur` in the cost book holds a
flat figure (the sheet's value for its reference car). `ModelSpec.co2_gkm` is
recorded and unread against the day this becomes a band lookup.

---

## 6. Worked example — sheet's reference car

Nissan Note e-Power (e13) 2023, 404 × 173 × 152 cm, auction ¥1 245 000,
at USD/JPY ≈ 158.9 and EUR/JPY effective ≈ 183.6.

| Step | Value |
|---|---|
| Auction price | ¥1 245 000 |
| Exporter fees (71 000 + 17 000 + 1 200) | ¥89 200 |
| Volume | 10.62 m³ |
| Freight ($166 × 10.62 × 158.9) | ¥280 288 |
| Freight insurance ($50) | ¥7 947 |
| **CNF price** | **¥1 622 435** |
| CNF price EUR | €8 834 |
| Bank transfer fees | €148.3 |
| Duty (0%) | €0 |
| VAT (19%) | €1 679 |
| Fixed expenses | €1 099 |
| **Total** | **€11 760** |

---

## 7. Implementation notes

- Do all arithmetic in a decimal/rational type, not floats, and round only at
  presentation. The sheet's displayed values are rounded per cell, so a port
  will differ from a screenshot by a euro or two — that is expected.
- The exchange rates are live in the sheet. The module needs a rate source
  (or injected rates) and records which rates a quote was produced with. The
  same now goes for prices: `costs.toml` is loaded once and stamped into the
  run (ADR 0003).
- `total_eur` excludes the buyer's own margin/profit — it is a landed cost.
- Insurance, car service, MOT and road tax are post-import ownership costs
  bundled into the total. This spec suggested a flag to exclude `B27:B29`;
  **the port deliberately has none.** Those lines are €181 of an €11,760 landed
  cost, and a switch that can only ever make a car look cheaper is a switch that
  gets left on. A book with those lines set to zero does the same job, on
  purpose and on the record.


Q1 - C
Q2 - A. But name file not just with dimensions but as an entity with co2 emissions
Q3 - B
Q4 - A
Q5 - B
Q6 - A
Q7 - C
Q8 - B
Q9 - A and add body_model_code to the fields list
Q10 - A
Q11 - A
Q12 - A
Q13 - Both numbers. I pay full VAT as a private person

Q14 - C
Q15 - A
Q16 - A
Q17 - C
Q18 - All three