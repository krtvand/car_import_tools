"""Filter -> UI URL building."""
from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs, urlparse

from banzai24 import config
from banzai24.config import AuctionFilters


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_base_path_is_make_model_transmission_slugs():
    f = AuctionFilters(make="MAZDA", model="CX-30", transmission="auto")
    assert config.base_path(f) == "/MAZDA/CX-30/transmissions-auto"


def test_base_path_omits_optional_segments():
    assert config.base_path(AuctionFilters(make="MAZDA", model=None, transmission=None)) == "/MAZDA"
    assert config.base_path(
        AuctionFilters(make="MAZDA", model="CX-30", transmission=None)
    ) == "/MAZDA/CX-30"


def test_ranges_map_to_start_end_params():
    url = config.build_search_url(
        AuctionFilters(year_start=2023, year_end=2024, mileage_end=55000,
                       engine_capacity_start=1.9)
    )
    q = _query(url)
    assert q["yearStart"] == ["2023"]
    assert q["yearEnd"] == ["2024"]
    assert q["mileageEnd"] == ["55000"]
    assert q["engineCapacityStart"] == ["1.9"]


def test_unset_filters_are_omitted_entirely():
    q = _query(config.build_search_url(AuctionFilters(year_start=None, mileage_end=None)))
    assert "yearStart" not in q
    assert "mileageEnd" not in q


def test_grade_origin_repeats_the_key():
    """Multi-select uses a repeated key, not a comma-joined value."""
    url = config.build_search_url(AuctionFilters(grade_origin=("4", "4.5", "5")))
    assert _query(url)["gradeOrigin"] == ["4", "4.5", "5"]
    assert "4%2C4.5" not in url  # not comma-joined


def test_source_and_country_always_present():
    q = _query(config.build_search_url(AuctionFilters()))
    assert q["source"] == ["auctions"]
    assert q["countryISO"] == ["JP"]


def test_archive_source_is_selectable():
    q = _query(config.build_search_url(AuctionFilters(source="archive")))
    assert q["source"] == ["archive"]


def test_the_saved_cx30_search_reproduces_the_phase0_reference_url():
    """The exact filter set verified live during Phase 0 recon.

    Asserted against the saved search rather than a constant, because the
    constant is gone: ``mazda-cx30.toml`` is now the only place that filter set
    lives, and an edit to it that breaks the verified URL is exactly what this
    should catch.
    """
    from banzai24 import search

    url = config.build_search_url(search.load("mazda-cx30").filters)
    parsed = urlparse(url)
    q = _query(url)

    assert parsed.netloc == "banzai24.com"
    assert parsed.path == "/MAZDA/CX-30/transmissions-auto"
    assert q["yearStart"] == ["2023"] and q["yearEnd"] == ["2023"]
    assert q["mileageEnd"] == ["55000"]
    assert q["engineCapacityStart"] == ["1.9"]
    assert sorted(q["gradeOrigin"]) == ["4", "4.5", "5"]
    assert q["source"] == ["auctions"]
    assert q["countryISO"] == ["JP"]


def test_run_slug_is_filesystem_safe():
    assert config.run_slug(AuctionFilters(make="MAZDA", model="CX-30")) == "MAZDA-CX-30"
    assert "/" not in config.run_slug(AuctionFilters(make="MERCEDES-BENZ", model="C/CLASS"))


def test_describe_lists_set_filters_only():
    text = config.describe(AuctionFilters(make="MAZDA", model=None, grade_origin=()))
    assert "make=MAZDA" in text
    assert "model=" not in text
    assert "grade_origin=" not in text


def test_an_omitted_filter_is_absent_rather_than_inherited():
    """The whole reason ``--no-defaults`` and ``DEFAULT_FILTERS`` are gone.

    A search that does not mention engine capacity must not pick one up from
    anywhere — there is no longer anywhere for it to come from, and this asserts
    that the dataclass itself has no car baked into it.
    """
    bare = AuctionFilters(make="TOYOTA")
    assert bare.model is None
    assert bare.transmission is None
    assert bare.engine_capacity_start is None
    assert bare.grade_origin == ()
    assert "engineCapacityStart" not in config.build_search_url(bare)