"""Run-directory artifacts, day selection and sheet naming — no network here."""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from banzai24 import fetch, lot_filters
from banzai24.config import AuctionFilters
from banzai24.fetch import FetchResult
from banzai24.lot_filters import LotFilters


def _lot(number: str = "47-1312-35159", image: str | None = "https://x/img") -> dict:
    return {"id": "019f-abc", "lot": {"number": number}, "auctImage": image}


def _dated(number: str, day: str, status: str = "LISTED") -> dict:
    """A lot as the list API returns it, with the two fields day selection reads."""
    return {
        "id": f"019f-{number}",
        "lot": {"number": number, "tradeDate": day, "tradeTime": "12:00"},
        "status": {"code": status},
        "auctImage": "https://x/img",
    }


def test_sheet_filename_uses_the_globally_unique_lot_number():
    assert fetch.sheet_filename(_lot()) == "47-1312-35159.jpg"


def test_sheet_filename_falls_back_to_uuid_when_lot_number_missing():
    assert fetch.sheet_filename({"id": "019f-abc"}) == "019f-abc.jpg"


def test_sheet_filename_sanitises_separators():
    name = fetch.sheet_filename({"lot": {"number": "47/1312 35159"}})
    assert "/" not in name and " " not in name


def test_run_dir_is_named_for_the_query_not_an_auction(tmp_path):
    """A run spans several houses and trade days, so it is named after filters."""
    run_dir = fetch._new_run_dir(AuctionFilters(make="MAZDA", model="CX-30"), root=tmp_path)
    assert run_dir.name.endswith("_MAZDA-CX-30")
    assert (run_dir / "sheets").is_dir()


def test_lots_json_keeps_payloads_verbatim(tmp_path):
    payloads = [
        {"items": [_lot("47-1-1")], "pagination": {"total": 2, "totalPages": 2, "perPage": 20}},
        {"items": [_lot("47-1-2")], "pagination": {"total": 2, "totalPages": 2, "perPage": 20}},
    ]
    filters = AuctionFilters()
    result = FetchResult(
        run_dir=fetch._new_run_dir(filters, root=tmp_path),
        lots=[i for p in payloads for i in p["items"]],
        pages_fetched=2, total_pages=2, total_lots=2, truncated=False,
    )
    path = fetch._write_lots_json(result, filters, "https://banzai24.com/x", payloads)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["pages"] == payloads            # untouched
    assert saved["search_url"] == "https://banzai24.com/x"
    assert saved["filters"]["make"] == filters.make
    assert saved["total_lots"] == 2


def test_truncated_flag_and_summary_warn_about_unfetched_pages(tmp_path):
    result = FetchResult(
        run_dir=tmp_path, lots=[_lot()], pages_fetched=1,
        total_pages=4, total_lots=61, truncated=True,
    )
    assert "TRUNCATED" in result.summary()


def test_summary_reports_counts(tmp_path):
    result = FetchResult(
        run_dir=tmp_path, lots=[_lot(), _lot()], pages_fetched=1,
        total_pages=1, total_lots=2, truncated=False,
        sheets_downloaded=1, sheets_skipped=1, sheets_missing=0,
    )
    text = result.summary()
    assert "2 lots" in text and "1 sheets downloaded" in text and "1 already present" in text


def test_sha256_changes_with_content(tmp_path):
    """The dedup key that stops us paying to re-read an unchanged sheet."""
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"sheet-one")
    b.write_bytes(b"sheet-two")
    assert fetch.sha256_of(a) != fetch.sha256_of(b)

    same = tmp_path / "c.jpg"
    same.write_bytes(b"sheet-one")
    assert fetch.sha256_of(same) == fetch.sha256_of(a)

def test_total_lots_counts_items_not_api_pagination():
    """Page 1 comes from the SSR payload, which carries no `pagination` block."""
    payload = {"items": [_lot("47-1-1"), _lot("47-1-2")]}
    assert len(payload["items"]) == 2


def test_ssr_shape_matches_api_shape():
    """Both sources feed the same normalisation, so the keys we use must match."""
    ssr_item = _lot()
    assert "auctImage" in ssr_item
    assert ssr_item["lot"]["number"]
    assert fetch.sheet_filename(ssr_item).endswith(".jpg")


def test_api_pagination_beats_ssr_page_count():
    """Page 1 is server-rendered with no totals; later pages carry the real ones."""
    payloads = [
        {"items": [_lot("a")]},                                            # SSR page 1
        {"items": [_lot("b")], "pagination": {"total": 340, "totalPages": 17}},
    ]
    found = next((p["pagination"] for p in payloads if p.get("pagination")), {})
    assert int(found["total"]) == 340
    assert int(found["totalPages"]) == 17


def test_falls_back_to_counted_lots_when_only_page_one_fetched():
    payloads = [{"items": [_lot("a"), _lot("b")]}]
    found = next((p["pagination"] for p in payloads if p.get("pagination")), {})
    lots = [i for p in payloads for i in p["items"]]
    assert (int(found.get("total") or 0) or len(lots)) == 2


# --- narrowing a run to the closest upcoming auction day ---------------------
#
# The fixture mirrors a real `source=auctions` result on 2026-08-08: the day's
# own lots have already been sold, then 08-11 and 08-12 are still to come.

TODAY = date(2026, 8, 8)
MIXED = [
    _dated("47-1312-35159", "2026-08-08", "SOLD"),
    _dated("50-1555-53023", "2026-08-08", "SOLD_BY_NEGO"),
    _dated("21-2055-8348", "2026-08-08", "NOT_SOLD"),
    _dated("65-1953-2377", "2026-08-11"),
    _dated("69-1252-30013", "2026-08-11"),
    _dated("55-1850-33152", "2026-08-12"),
]


def test_nearest_day_skips_a_day_that_has_already_traded():
    """min(tradeDate) would pick 08-08, whose lots are all sold — the whole point."""
    assert fetch.nearest_trade_date(MIXED, TODAY) == "2026-08-11"


def test_todays_day_is_kept_while_its_lots_are_still_listed():
    lots = [_dated("a", "2026-08-08"), *MIXED]
    assert fetch.nearest_trade_date(lots, TODAY) == "2026-08-08"


def test_lots_on_nearest_day_drops_both_later_days_and_finished_ones():
    lots, day = fetch.lots_on_nearest_day(MIXED, TODAY)
    assert day == "2026-08-11"
    assert [l["lot"]["number"] for l in lots] == ["65-1953-2377", "69-1252-30013"]


def test_no_upcoming_day_keeps_everything_rather_than_emptying_the_run():
    """An `archive` search has no upcoming day; a zero-lot run would look broken."""
    sold = [_dated("a", "2026-07-30", "SOLD"), _dated("b", "2026-08-07", "NOT_SOLD")]
    lots, day = fetch.lots_on_nearest_day(sold, TODAY)
    assert day is None
    assert lots == sold


def test_lots_without_a_trade_date_are_never_upcoming():
    assert fetch.nearest_trade_date([_lot("no-date")], TODAY) is None


def test_tokyo_and_cyprus_disagree_about_the_date_every_evening():
    """The boundary the JST comparison exists for: 18:00 in Cyprus is tomorrow in Tokyo."""
    evening = datetime(2026, 8, 8, 21, 0, tzinfo=ZoneInfo("Europe/Nicosia"))
    assert evening.date() == date(2026, 8, 8)
    assert evening.astimezone(fetch.JAPAN_TZ).date() == date(2026, 8, 9)


def test_day_selection_defaults_to_tokyos_date(monkeypatch):
    """`tradeDate` is a Japanese date, so the default "today" must be one too.

    Guards the regression directly: swap `japan_today` back for the machine's
    local date and the second case picks the wrong day for six hours a night.
    """
    monkeypatch.setattr(fetch, "japan_today", lambda: date(2026, 8, 8))
    assert fetch.nearest_trade_date(MIXED) == "2026-08-11"

    monkeypatch.setattr(fetch, "japan_today", lambda: date(2026, 8, 12))
    assert fetch.nearest_trade_date(MIXED) == "2026-08-12"


def test_later_day_seen_is_the_signal_to_stop_paging():
    assert fetch.has_later_day(MIXED, "2026-08-11", TODAY) is True
    assert fetch.has_later_day(MIXED[:5], "2026-08-11", TODAY) is False


def test_nearest_day_complete_stops_paging_once_a_later_day_appears():
    page1 = {"items": MIXED[:4]}          # 08-11 has started, nothing beyond yet
    page2 = {"items": MIXED[4:]}          # 08-12 shows up → 08-11 is complete
    assert fetch._nearest_day_complete([page1], TODAY) is False
    assert fetch._nearest_day_complete([page1, page2], TODAY) is True


def test_the_day_is_chosen_before_the_filter_runs():
    """Narrow to the day, then filter within it — never search later days.

    08-11 is the nearest day and holds only DMEJ3R. Asking for DMEJ3P gives an
    empty run: the DMEJ3P on 08-12 is not a substitute, because the question is
    what to look at for the next auction.
    """
    lots = [
        _dated("a", "2026-08-11") | {"bodyModelCode": "DMEJ3R"},
        _dated("b", "2026-08-12") | {"bodyModelCode": "5AA-DMEJ3P"},
    ]
    on_day, day = fetch.lots_on_nearest_day(lots, TODAY)
    kept, rejected = lot_filters.split(on_day, LotFilters(body_model_code=("DMEJ3P",)))

    assert day == "2026-08-11"
    assert kept == []
    assert len(rejected) == 1


def test_a_filter_never_makes_the_run_turn_another_page():
    """The boundary is judged over every lot, so an emptied day still stops paging."""
    page1 = {"items": [
        _dated("a", "2026-08-11") | {"bodyModelCode": "DMEJ3R"},
        _dated("b", "2026-08-12") | {"bodyModelCode": "DMEJ3R"},
    ]}
    assert fetch._nearest_day_complete([page1], TODAY) is True


def test_summary_names_the_day_and_the_lots_set_aside(tmp_path):
    result = FetchResult(
        run_dir=tmp_path, lots=[_dated("a", "2026-08-11")], pages_fetched=1,
        total_pages=3, total_lots=40, truncated=False,
        trade_date="2026-08-11", lots_other_days=4,
    )
    text = result.summary()
    assert "on 2026-08-11" in text
    assert "4 lots on other days skipped" in text


def test_lots_json_records_the_chosen_day_but_keeps_every_page_verbatim(tmp_path):
    payloads = [{"items": MIXED}]
    filters = AuctionFilters()
    selected, day = fetch.lots_on_nearest_day(MIXED, TODAY)
    result = FetchResult(
        run_dir=fetch._new_run_dir(filters, root=tmp_path),
        lots=selected, pages_fetched=1, total_pages=1, total_lots=len(MIXED),
        truncated=False, trade_date=day, lots_other_days=len(MIXED) - len(selected),
    )
    saved = json.loads(
        fetch._write_lots_json(result, filters, "https://banzai24.com/x", payloads)
        .read_text(encoding="utf-8")
    )
    assert saved["trade_date"] == "2026-08-11"
    assert saved["lots_selected"] == ["65-1953-2377", "69-1252-30013"]
    assert saved["lots_other_days"] == 4
    assert saved["pages"] == payloads    # the discarded days are still on disk
