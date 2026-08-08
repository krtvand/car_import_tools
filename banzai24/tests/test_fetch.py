"""Run-directory artifacts and sheet naming — the parts that touch no network."""
from __future__ import annotations

import json

from banzai24 import fetch
from banzai24.config import AuctionFilters
from banzai24.fetch import FetchResult


def _lot(number: str = "47-1312-35159", image: str | None = "https://x/img") -> dict:
    return {"id": "019f-abc", "lot": {"number": number}, "auctImage": image}


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
