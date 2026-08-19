"""Building the review report.

Two things carry the whole value of this page, and they are what is tested here:

1. **Flagged lots sort first.** A report where the one car with a chassis
   mismatch sits nineteenth is a report that hid the finding it exists to
   surface, and it would still look completely fine.
2. **Nothing external is referenced.** The report is meant to open by
   double-click after the run directory has been copied anywhere. One stray
   ``<img src="sheets/…">`` and it silently degrades into a page of broken
   images — silently, because the HTML still renders.

The rest is field-level: the sheet's numbers must reach the page, and the
cross-check marks must qualify the value they belong to.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from banzai24 import db, report
from banzai24.models import AuctionLot, SheetExtraction

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()
    return engine


@pytest.fixture
def no_cyprus(monkeypatch):
    """Report without the bazaraki join.

    The Cyprus figure comes from the *other* project's database, which the
    report treats as optional context. Tests that are not about it pass an empty
    pricer so they neither depend on that file existing nor on what is in it.
    """
    monkeypatch.setattr(report, "CyprusPricer", lambda *a, **kw: _EmptyPricer())
    return None


@pytest.fixture
def no_bid_prices(monkeypatch):
    """Report without the bid tables.

    Same reasoning as :func:`no_cyprus`: the price tables are operator-authored
    files that tests not about them should neither depend on nor be perturbed by
    when you re-tune a number in one.
    """
    monkeypatch.setattr(report, "BidPricer", lambda *a, **kw: _EmptyPricer())
    return None


class _EmptyPricer:
    available = False
    reason = None

    def for_lot(self, lot, extraction=None):
        return None


def _lot(number: str = "55-1850-33152", **overrides) -> AuctionLot:
    """The fixture sheet's own lot, as the list API described it.

    Same values as ``test_sheets._lot`` — the real API row for the saved sheet,
    including the 15,000-vs-15,415 rounding and the masked chassis.
    """
    base = {
        "lot_number": number, "lot_short": number.rsplit("-", 1)[-1],
        "banzai_id": "019fded9-2a2a-714c-96e3-fcff2823fcb7",
        "auction_id": 55, "auction_name": "CAA Chubu",
        "trade_date": date(2026, 8, 12), "trade_time": "12:00",
        "mark": "MAZDA", "model": "CX-30", "modification": "20S PROACTIVE TOURING",
        "body_model_code": "DMEJ3P", "body_number": "DMEJ3P-10**52",
        "grade_origin": "5", "mileage_km": 15000, "status_code": "LISTED",
        "registration_year": 2023, "registration_month": 1,
        "start_price_jpy": 1180000, "currency": "JPY",
        "sheet_status": "extracted",
    }
    return AuctionLot(**{**base, **overrides})


def _extraction(lot_number: str = "55-1850-33152", **overrides) -> SheetExtraction:
    base = {
        "lot_number": lot_number,
        "extracted_at": datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc),
        "model_id": "claude-opus-5", "sheet_sha256": "abc", "raw_json": "{}",
        "sheet_grade": "5", "exterior_grade": "A", "interior_grade": "B",
        "sheet_mileage_km": 15415, "chassis_full": "DMEJ3P-103452",
        "first_registration_raw": "R5年1月", "shaken_expiry_raw": "令和8年1月",
        "damage_marks": json.dumps([{"panel": "right rear", "code": "A1"},
                                    {"panel": "bonnet", "code": "トビA"}],
                                   ensure_ascii=False),
        "equipment": json.dumps(["純正メーカーナビTV"], ensure_ascii=False),
        "warnings_ja": "ﾋﾟSD欠品", "warnings_en": "Navi SD card missing",
        "inspector_notes_ja": "ハンドルすれ",
        "inspector_notes_en": "Steering wheel scuffed",
        "private_car_note": "自家用", "confidence": 0.95,
    }
    return SheetExtraction(**{**base, **overrides})


def _view(lot=None, extraction=None, **overrides) -> report.LotView:
    """A rendered view with its flags and cross-checks derived, as collect() does."""
    from banzai24 import sheets

    lot = lot or _lot()
    checks = sheets.cross_check(extraction, lot) if extraction else None
    return report.LotView(
        lot=lot, extraction=extraction, checks=checks,
        flags=report._flags(extraction, checks),
        **overrides,
    )


# --- flags -------------------------------------------------------------------


def test_clean_extracted_lot_is_not_flagged():
    assert _view(extraction=_extraction()).flags == []


def test_chassis_mismatch_is_flagged():
    """The strongest cross-check: a wrong chassis is a different car."""
    view = _view(extraction=_extraction(chassis_full="DMEJ3P-109999"))
    assert [f.key for f in view.flags] == ["mismatch"]
    assert "chassis" in view.flags[0].label


def test_blank_shaken_is_not_a_flag():
    """A real cost, but the common case on export lots — it would badge most of
    the page and bury the findings that are actually unusual. The card still
    prints it against the 車検 field."""
    assert _view(extraction=_extraction(shaken_expiry_raw=None)).flags == []


def test_low_confidence_is_flagged():
    view = _view(extraction=_extraction(confidence=0.6))
    assert "low-confidence" in {f.key for f in view.flags}


def test_an_unread_sheet_earns_no_badge_because_its_group_already_says_so():
    """A pending sheet is a gap in the report, not something wrong with the car.

    It used to carry a ``not-read`` badge. The *unconfirmed* group says the same
    thing now, for every card under it, and a badge repeating it was the fact
    printed twice. It must still not inflate the flagged count, or a freshly
    fetched run reports every lot as flagged and the number stops meaning
    anything.
    """
    view = _view(lot=_lot(sheet_status="pending"))
    assert view.flags == []

    built = report.Report(run_dir=Path("."), views=[view])
    assert built.flagged == 0
    assert built.unread == 1


def test_a_grade_disagreement_is_not_badged_because_it_is_a_requirement():
    """Grade and mileage moved out of the badge vocabulary and into the groups.

    The sheet disagreeing with the API about the grade is now judged as a
    requirement — the lot lands in *fails a requirement* with the figures
    printed against the value that broke — so a second badge saying "grade
    mismatch" would be the same finding in two vocabularies.
    """
    view = _view(extraction=_extraction(sheet_grade="3.5"))
    assert view.checks.grade is False          # the cross-check still runs
    assert view.flags == []                    # …and no longer earns a badge


# --- sorting -----------------------------------------------------------------


def test_flagged_lots_sort_above_clean_ones_whatever_the_trade_order():
    """The whole point of the ordering, so it is tested against the tiebreak.

    The flagged lot trades *last*, so a report sorted only by trade time would
    put it at the bottom — which is the failure this guards against. The clean
    lot sorts last of the three even though it trades first: a lot that was
    read and came back with nothing wrong is the one that needs you least, and
    an unread sheet is still outstanding work even though it is not a finding.
    """
    clean = _view(lot=_lot("47-1312-00001", trade_time="09:00"),
                  extraction=_extraction("47-1312-00001"))
    mismatched = _view(lot=_lot("47-1312-00003", trade_time="23:00"),
                       extraction=_extraction("47-1312-00003",
                                              chassis_full="DMEJ3P-100000"))

    ordered = sorted([clean, mismatched], key=lambda v: v.sort_key)
    assert [v.lot.lot_number for v in ordered] == [
        "47-1312-00003",   # mismatch — this may not even be the right car
        "47-1312-00001",   # clean, and trades first, and still sorts last
    ]


def test_equal_severity_falls_back_to_trade_order():
    late = _view(lot=_lot("47-1312-00009", trade_time="16:00"),
                 extraction=_extraction("47-1312-00009"))
    early = _view(lot=_lot("47-1312-00008", trade_time="08:30"),
                  extraction=_extraction("47-1312-00008"))
    ordered = sorted([late, early], key=lambda v: v.sort_key)
    assert [v.lot.trade_time for v in ordered] == ["08:30", "16:00"]


# --- grouping ----------------------------------------------------------------


def _judged(view, definition=None):
    """A view with its assessment attached, as ``collect`` does."""
    from banzai24.requirements import judge

    definition = definition or _definition()
    view.assessment = judge(definition.filters, definition.requirements,
                            view.lot, view.extraction)
    view.requirements = definition.requirements
    return view


def _definition():
    from banzai24.config import AuctionFilters
    from banzai24.requirements import SheetRequirements
    from banzai24.search import SearchDefinition

    return SearchDefinition(
        name="fixture",
        filters=AuctionFilters(make="MAZDA", model="CX-30", mileage_end=55_000),
        requirements=SheetRequirements(no_damage_codes=("W", "X", "欠")),
    )


def test_the_group_outranks_severity_in_the_ordering():
    """The group is the question the page answers, so it sorts first — even
    against a mismatch, which is the loudest badge there is.

    A lot that may not be the right car still belongs under the heading for
    cars that meet the requirements, if it meets them. Burying a clean,
    biddable lot below a disqualified one because the disqualified one wears a
    red badge would invert the page's whole purpose.
    """
    clean_but_mismatched = _judged(_view(
        lot=_lot("47-1312-00001", trade_time="09:00"),
        extraction=_extraction("47-1312-00001", chassis_full="DMEJ3P-100000")))
    failed = _judged(_view(
        lot=_lot("47-1312-00002", trade_time="08:00"),
        extraction=_extraction("47-1312-00002", damage_marks=json.dumps(
            [{"panel": "door", "code": "W2"}]))))

    assert clean_but_mismatched.flags     # it is badged…
    ordered = sorted([failed, clean_but_mismatched], key=lambda v: v.sort_key)
    assert [v.lot.lot_number for v in ordered] == [
        "47-1312-00001",   # …and still sorts above the disqualified car
        "47-1312-00002",
    ]


def test_empty_groups_are_not_rendered_as_headings():
    """A "fails a requirement · 0" heading on a morning where nothing failed is
    a heading you learn to skip. The counts are in the page header anyway."""
    built = report.Report(
        run_dir=Path("."), views=[_judged(_view(extraction=_extraction()))],
        definition=_definition())
    assert [g.key for g in built.grouped] == ["meets"]


def test_a_run_that_named_no_search_renders_one_ungrouped_list():
    """Runs fetched before saved searches were files. The report must not invent
    a verdict for lots that were never judged against anything."""
    built = report.Report(run_dir=Path("."), views=[_view(extraction=_extraction())])
    assert built.grouped == []
    html = report.render(built)
    assert "meets all requirements" not in html
    assert "named no saved search" in html


def test_the_group_headings_and_the_reason_reach_the_page():
    views = [
        _judged(_view(lot=_lot("47-1312-00001"),
                      extraction=_extraction("47-1312-00001"))),
        _judged(_view(lot=_lot("47-1312-00002"), extraction=_extraction(
            "47-1312-00002",
            damage_marks=json.dumps([{"panel": "left front door", "code": "W2"}])))),
        _judged(_view(lot=_lot("47-1312-00003", sheet_status="pending"))),
    ]
    built = report.Report(run_dir=Path("."), views=sorted(views, key=lambda v: v.sort_key),
                          definition=_definition())
    html = report.render(built)

    assert [g.key for g in built.grouped] == ["meets", "unconfirmed", "fails"]
    assert "meets all requirements" in html
    assert "W2 on the left front door" in html
    # the offending mark is picked out, not left for you to re-scan the list for
    assert 'class="mark banned"' in html


def test_a_failing_lot_still_carries_its_bid_price():
    """The arithmetic does not depend on the verdict: a car you will not buy is
    still one whose price you may want to know."""
    from banzai24.bidding import BidQuote

    view = _judged(_view(
        extraction=_extraction(damage_marks=json.dumps([{"panel": "door", "code": "W2"}])),
        quote=BidQuote(max_bid=1_855_000, extra_costs=6_000, bid_reduced=1_849_000)))
    assert view.group == "fails"
    assert "1,849,000" in _render([view])


# --- rendering ---------------------------------------------------------------


def _render(views) -> str:
    return report.render(report.Report(run_dir=Path("runs/fixture"), views=views))


def test_no_external_image_references_remain():
    """Self-contained or it does not survive being copied out of the run dir.

    Checked as "every src is a data URI" rather than "no http appears": the page
    legitimately *links* to banzai24's lot pages, and those hyperlinks are never
    loaded. It is the references the browser fetches on open that must all be
    inline.
    """
    view = _view(extraction=_extraction(), sheet_uri="data:image/jpeg;base64,AAAA")
    html = _render([view])

    sources = re.findall(r'src="([^"]*)"', html)
    assert sources, "the fixture view has a sheet, so there should be an <img>"
    assert all(src.startswith("data:") for src in sources), sources

    # Nothing else the browser would go out and fetch on open.
    assert "<script" not in html
    assert "<link" not in html
    assert "@import" not in html


def test_the_sheets_own_numbers_reach_the_page():
    html = _render([_view(extraction=_extraction(), sheet_uri="data:image/jpeg;base64,AA")])

    assert "15,415 km" in html          # the sheet's exact mileage, not the API's
    assert "DMEJ3P-103452" in html      # unmasked chassis
    assert "DMEJ3P-10**52" in html      # …next to the masked one it was checked against
    assert "ﾋﾟSD欠品" in html            # half-width katakana, verbatim
    assert "トビA" in html               # a code outside the printed legend, verbatim
    assert "キズ — scratch" in html      # the legend, joined in for the codes that are


def test_a_mismatch_is_marked_against_the_value_it_disagrees_with():
    """The ``bad`` class is what the CSS turns into a red ✗ next to the number."""
    view = _view(extraction=_extraction(chassis_full="DMEJ3P-109999"),
                 sheet_uri="data:image/jpeg;base64,AA")
    html = _render([view])
    assert re.search(r'<code class="bad">DMEJ3P-109999</code>', html)


def test_a_lot_without_a_sheet_says_so_rather_than_rendering_a_gap():
    html = _render([_view(lot=_lot(sheet_status="no_sheet"))])
    assert "no sheet for this lot" in html
    assert "<img" not in html


def test_vehicle_history_is_glossed_in_english_beside_the_japanese():
    """車歴 is the one field where the printed word alone means nothing to a
    non-Japanese reader — 自家用 is good news, レンタカー is not."""
    html = _render([_view(extraction=_extraction())])
    assert "自家用" in html
    assert "private use" in html

    rental = _render([_view(extraction=_extraction(
        private_car_note=None, rental_car_note="レンタカー"))])
    assert "ex-rental" in rental
    assert "warn" in rental


def test_the_warnings_box_is_translated_beside_the_japanese():
    """ﾋﾟSD欠品 is the box that costs the buyer money, written in shorthand no
    non-Japanese reader can price. The Japanese still prints beside it."""
    html = _render([_view(extraction=_extraction())])
    assert "ﾋﾟSD欠品" in html
    assert "Navi SD card missing" in html
    assert "Warnings 注意事項欄" in html


def test_a_warnings_box_extracted_before_the_translation_still_renders():
    """Rows stored by the earlier prompt have no warnings_en, and re-reading a
    sheet costs money — the Japanese must stand on its own until then."""
    html = _render([_view(extraction=_extraction(warnings_en=None))])
    assert re.search(r"ﾋﾟSD欠品\s*</dd>", html)


def test_unknown_history_wording_still_renders_without_a_gloss():
    html = _render([_view(extraction=_extraction(private_car_note="移動販売車"))])
    assert re.search(r"移動販売車\s*</dd>", html)   # printed, with no gloss appended


def test_japanese_is_escaped_not_mangled():
    """Autoescape must not touch the Japanese, and must still escape markup."""
    view = _view(extraction=_extraction(warnings_ja="<b>取保</b> & 後送"))
    html = _render([view])
    assert "&lt;b&gt;取保&lt;/b&gt; &amp; 後送" in html


# --- collecting from a saved run --------------------------------------------


def test_collect_reads_the_run_and_joins_the_database(tmp_path, temp_db, no_cyprus,
                                                      no_bid_prices):
    """End to end over the saved fixture run, with one lot extracted."""
    run_dir = tmp_path / "run"
    (run_dir / "sheets").mkdir(parents=True)
    (run_dir / "lots.json").write_text(
        (FIXTURES / "lots_run.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    from banzai24 import normalize

    rows, _ = normalize.load_run(run_dir)
    db.upsert_lots(rows)
    first = rows[0]["lot_number"]

    with Session(temp_db) as session:
        session.add(_extraction(first, chassis_full=None))
        session.commit()

    built = report.collect(run_dir)

    assert len(built.views) == len(rows)
    assert built.missing == []
    assert {v.lot.lot_number for v in built.views} == {r["lot_number"] for r in rows}
    assert any(v.extraction for v in built.views)

    html = report.render(built)
    assert all(src.startswith("data:") for src in re.findall(r'src="([^"]*)"', html))


def test_a_run_lot_missing_from_the_database_still_renders(tmp_path, temp_db, no_cyprus,
                                                           no_bid_prices):
    """Skipping `normalize` should thin the report, not empty it — and say so."""
    run_dir = tmp_path / "run"
    (run_dir / "sheets").mkdir(parents=True)
    (run_dir / "lots.json").write_text(
        (FIXTURES / "lots_run.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    built = report.collect(run_dir)

    assert built.views and len(built.missing) == len(built.views)
    assert "not in" in built.summary()
    assert "run <code>normalize</code>" in report.render(built)


def test_run_report_writes_a_standalone_file(tmp_path, temp_db, no_cyprus, no_bid_prices):
    run_dir = tmp_path / "run"
    (run_dir / "sheets").mkdir(parents=True)
    (run_dir / "lots.json").write_text(
        (FIXTURES / "lots_run.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    built = report.run_report(run_dir)

    assert built.output == run_dir / "report.html"
    html = built.output.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "</html>" in html


# --- the Cyprus join ---------------------------------------------------------


def test_cyprus_comparable_comes_from_the_bazaraki_records():
    """The two databases join on make/model strings, spelled differently.

    banzai24 writes ``MAZDA``/``CX-30`` and bazaraki writes ``Mazda``/``CX-30``;
    the match has to survive that, which is why it goes through
    :func:`bazaraki.analysis.filter_model` rather than an equality test.
    """
    from bazaraki.analysis import CarRecord

    records = [
        CarRecord(ad_id=i, price=18_000 + i * 100, year=2023, mileage_km=15_000,
                  make="Mazda", model="CX-30")
        for i in range(8)
    ]
    comp = report.CyprusPricer(records=records).for_lot(_lot())

    assert comp is not None and comp.n == 8
    assert comp.median == pytest.approx(18_350)
    assert "€18,350" in comp.describe()


def test_a_lot_without_year_or_mileage_has_no_comparable_query():
    """Distinct from asking and finding nothing — there is nothing to ask."""
    from bazaraki.analysis import CarRecord

    pricer = report.CyprusPricer(records=[
        CarRecord(ad_id=1, price=18_000, year=2023, mileage_km=15_000,
                  make="Mazda", model="CX-30")
    ])
    assert pricer.for_lot(_lot(mileage_km=None)) is None


def test_no_cyprus_data_is_reported_not_raised(monkeypatch):
    """bazaraki.db is optional context — a report without it is still the point."""
    def boom():
        raise FileNotFoundError("bazaraki.db")

    import bazaraki.db as bazaraki_db
    monkeypatch.setattr(bazaraki_db, "all_listings", boom)

    pricer = report.CyprusPricer()
    assert pricer.available is False
    assert "bazaraki.db" in pricer.reason
    assert pricer.for_lot(_lot()) is None


# --- the bid price -----------------------------------------------------------
#
# The arithmetic and its seven null reasons are :mod:`test_bidding`'s job, which
# tests them without rendering anything. These two only check that the answer
# reaches the page — the seam between a computed quote and the card.


def test_the_money_block_reaches_the_card():
    from banzai24.bidding import BidQuote

    quote = BidQuote(max_bid=1_855_000, extra_costs=12_000, bid_reduced=1_843_000,
                     house="U Tokyo")
    html = _render([_view(extraction=_extraction(), quote=quote)])

    assert "max bid ¥1,855,000" in html
    assert "area (U Tokyo) −¥12,000" in html
    assert "bid reduced ¥1,843,000" in html


def test_a_lot_that_cannot_be_priced_prints_why_instead_of_a_number():
    """The reason takes the number's place on the card. Whatever *is* known
    still prints beside it — the house cost is useful on a card that cannot
    price the car."""
    from banzai24.bidding import BidQuote

    quote = BidQuote(extra_costs=12_000, house="U Tokyo",
                     reason="sheet does not say rental or private")
    html = _render([_view(quote=quote)])

    assert "sheet does not say rental or private" in html
    assert "area (U Tokyo) −¥12,000" in html
    assert "bid reduced" not in html


def test_a_missing_bid_table_is_said_once_in_the_header_not_on_every_card():
    """Sixty-two identical "bid prices not loaded" lines is noise; the cards stay
    silent and the header carries it."""
    built = report.Report(run_dir=Path("runs/fixture"),
                          views=[_view(extraction=_extraction())],
                          bid_reason="bid prices not loaded")
    html = report.render(built)

    assert html.count("bid prices not loaded") == 1
    assert "no bid price on any card below" in html
    assert "<dt>Bid</dt>" not in html


def test_a_header_note_does_not_claim_cards_are_blank_when_they_are_not():
    """A mis-edited alias file is worth saying out loud, but it suppresses
    nothing — only the six houses it covers stop matching. The note must not
    tell you to stop looking at cards that are priced."""
    from banzai24.bidding import BidQuote

    built = report.Report(
        run_dir=Path("runs/fixture"),
        views=[_view(extraction=_extraction(), quote=BidQuote(reason="missing year"))],
        bid_reason="auction aliases not loaded: aliases.csv line 2: U Tokyo is aliased twice",
    )
    html = report.render(built)

    assert "aliased twice" in html
    assert "no bid price on any card below" not in html
