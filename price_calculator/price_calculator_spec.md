# Imported car price calculator — spec

Computes the **expected landed price in EUR** of a car bought at a Japanese
auction and imported to Cyprus.

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
| `road_tax_eur` | EUR | `Calculator!B28` | Depends on CO₂ emissions — see §5 |
| `usd_jpy` | JPY per USD | `Calculator!B33` | Live: `GOOGLEFINANCE("CURRENCY:USDJPY")` |
| `eur_jpy_market` | JPY per EUR | `Calculator!B32` | Live: `GOOGLEFINANCE("CURRENCY:EURJPY")` |
| `with_freight_insurance` | bool | — | Optional, default **on** (the sheet always includes it) |

Dimensions are the only car params that affect the price beyond the auction
price: they drive the freight cost via shipping volume.

---

## 2. Rates and constants

### Exchange rates

```
eur_jpy_effective = eur_jpy_market - 2      # Calculator!B32, broker spread
```

`usd_jpy` is used as-is. The `-2` haircut on EUR/JPY is a deliberate
conservative margin on the conversion actually obtained by the bank.

### Tax rates (`Calculator!B34:B35`)

| Constant | Value |
|---|---|
| `VAT_RATE` | 19% (Cyprus) |
| `DUTY_RATE` | 0% |

> Note on `Calculator!B35` (Duty): *"Starting January 2026, import duty on cars
> originating from Japan is reduced to zero, provided that all required
> documentation is submitted."* Keep `DUTY_RATE` configurable — it is 0 only
> while the documentation requirement is met.

### Exporter fees (`Exporter service fees` sheet)

Tiered service fee by auction price, JPY (`A3:C6`, looked up with an
approximate/sorted `VLOOKUP` on `auction_price`):

| Auction price from | to | Service fee |
|---|---|---|
| ¥1 | ¥1 000 000 | ¥56 000 |
| ¥1 000 001 | ¥1 500 000 | ¥71 000 |
| ¥1 500 001 | ¥2 000 000 | ¥91 000 |
| ¥2 000 001 | ¥9 000 000 | ¥111 000 |

Plus, always:

| Constant | Cell | Value |
|---|---|---|
| `EXPORTER_FIXED_FEE` | `C8` | ¥17 000 (= 10 000 + 7 000) |
| `CERTIFICATE_OF_ORIGIN` | `C9` | ¥1 200 |
| `RORO_PRICE_PER_M3_USD` | `C10` | $166 per m³ |
| `FREIGHT_INSURANCE_USD` | `B19` | $50 (flat) |

Behaviour outside the table: prices above ¥9 000 000 fall back to the last tier
(¥111 000) under sorted `VLOOKUP`; below ¥1 the lookup fails. Implementation
should treat `auction_price <= 0` as invalid and clamp/flag prices above
¥9 000 000.

### Fixed expenses in Cyprus, EUR (`Calculator!B21:B29`)

| Item | Cell | EUR |
|---|---|---|
| SVA test | `B22` | 140 |
| MOT | `B23` | 35 |
| Registration in Department of Road Transport | `B24` | 200 (= 150 department + 50 agent) |
| Delivery order and other customs clearance expenses | `B25` | 513 (= 339 + 10 + 10 + 10 + 15 + 10 + 119) |
| Number plates | `B26` | 30 |
| Car service (oil, filters) | `B27` | 120 |
| Road tax | `B28` | 11 *(car-dependent, see §5)* |
| Insurance | `B29` | 50 |
| **Total** | `B21` | **1 099** *(with road tax = 11)* |

Everything except road tax is a constant. Model as
`FIXED_EXPENSES_BASE_EUR = 1 088` plus `road_tax_eur`.

### Bank transfer fees (`Calculator!B10`)

```
bank_transfer_fees_eur = cnf_price_eur * 0.01 + 60
```

- `0.01` — Revolut FX/exchange fee (1%)
- `60` — two international payments × €30

---

## 3. Calculation

All intermediate money is JPY until the CNF conversion, then EUR.

### 3.1 CNF price (JPY) — `Calculator!B15`

```
exporter_fees_jpy = tier_fee(auction_price)      # Exporter service fees!A3:C6
                  + 17_000                       # fixed fee
                  + 1_200                        # certificate of origin

volume_m3   = (length_cm * width_cm * height_cm) / 1_000_000

freight_jpy = 166 * usd_jpy * volume_m3          # RORO, $166/m³

insurance_jpy = 50 * usd_jpy                     # if with_freight_insurance

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
bank_transfer_fees = cnf_price_eur * 0.01 + 60           # B10
duty               = cnf_price_eur * DUTY_RATE           # B11
vat                = (cnf_price_eur + duty) * VAT_RATE   # B12
fixed_expenses     = FIXED_EXPENSES_BASE_EUR + road_tax  # B13 -> B21

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
rates_used: { usd_jpy, eur_jpy_market, eur_jpy_effective }
```

---

## 5. Road tax

Road tax is the one fixed expense that varies per car; it is a function of CO₂
emissions (g/km). From the sheet comment on `Calculator!A28`:

- Calculator: <https://cyprusgloballogistics.com/cyprus-road-tax-caclulator/>
- CO₂ figures for a given model can be looked up in the drom.ru catalog,
  e.g. <https://www.drom.ru/catalog/mazda/cx-60/431143/>

Until CO₂ per model is available, default `road_tax_eur = 11` (the sheet's
value for the reference car).

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
  (or injected rates) and should record which rates a quote was produced with.
- `total_eur` excludes the buyer's own margin/profit — it is a landed cost.
- Insurance, car service, MOT and road tax are post-import ownership costs
  bundled into the total; if the module is used to price *lots* rather than
  *ownership*, expose a flag to exclude `B27:B29`.


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