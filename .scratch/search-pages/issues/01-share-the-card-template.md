# 01 — Split the card and the styles out of report.html.j2

Status: done

`banzai24/templates/report.html.j2` is one 637-line file: the CSS is inline in
`<style>`, and `{% macro card(view) %}` sits at line 230 with the page body under
it. The search page and the past page need that macro and those styles.

## Do

- Extract `{% macro card(view) %}` and `{% macro diagram_notes(notes) %}` into
  `banzai24/templates/_card.html.j2`.
- Extract the contents of `<style>` into `banzai24/templates/_card.css.j2` — as a
  macro or an include, whichever reads better — split so that the rules the card
  needs can be pulled in by a page that is not the report (the `.wrap`, `body`
  and `header` rules can stay with the report if they do not belong to a card).
- `report.html.j2` imports both. Its rendered output must not change.
- The damage-code legend and the footer stay in `report.html.j2` — the report
  owns them; the search page decides separately whether it wants a legend.

## Done when

`banzai24 report` on an existing run produces a `report.html` identical to the
one already on disk but for the generated-at stamp, and `_card.html.j2` renders
standalone when given a `LotView` and the same Jinja filters
(`report._environment()` registers `yen`, `km`, `check`, `verdict`).

## Comments

**2026-09-20 — implemented.**

- `banzai24/templates/_card.css.j2` — `tokens()` (palette + box-sizing reset) and
  `styles()` (group headings, cards, sheet/photos/fields/marks/money, `details`,
  and the 720px media query). `body`, `.wrap`, `header` and `footer` stayed in
  `report.html.j2`: a search page is not 1100px of report and should not inherit
  a width from one.
- `banzai24/templates/_card.html.j2` — `diagram_notes(notes)` and
  `card(view, jpy_per_eur=None)`, with the diagram comment moved across with it.
- The card gained `jpy_per_eur` as a **parameter**. It was reading it as a render
  global, and a macro imported without context cannot see those — the euro line
  under a start price would have silently vanished on the new pages.
  `report.html.j2` now calls `card(view, jpy_per_eur)`.
- The damage-code legend and the footer stayed with the report, as specified.

Verified: the rendered report's CSS is the same 80 rules before and after (same
multiset, comments stripped), and the body is line-for-line identical ignoring
blank lines. Not byte-identical: the three `footer` rules now sit with the other
page chrome, above the card rules instead of below them, and two blank lines
left by a moved comment are gone. `footer dl` beats `dl` on specificity either
way, so the order does not matter to the cascade.

`uv run pytest` — 896 passed, 1 failed. The failure is
`banzai24/tests/test_config.py::test_the_saved_cx30_search_reproduces_the_phase0_reference_url`
and it is **pre-existing and unrelated**: it asserts `mileageEnd=60000` for the
CX-30 while `searches/mazda-cx30.toml` now ends its last band at 50,000. That
test exists to catch exactly this kind of edit — someone should decide whether
the 50,000 bound is intended and then fix the band or the test.
