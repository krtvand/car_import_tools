"""Tests for filter -> search URL construction."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from bazaraki import config
from bazaraki.config import CarFilters


# --- base_path --------------------------------------------------------------

def test_base_path_category_only():
    assert config.base_path(CarFilters()) == (
        "/car-motorbikes-boats-and-parts/cars-trucks-and-vans/"
    )


def test_base_path_make_and_model():
    f = CarFilters(make="mazda", model="cx-30")
    assert config.base_path(f) == (
        "/car-motorbikes-boats-and-parts/cars-trucks-and-vans/mazda/cx-30/"
    )


def test_base_path_make_only():
    f = CarFilters(make="mazda")
    assert config.base_path(f).endswith("/mazda/")


def test_base_path_model_without_make_raises():
    with pytest.raises(ValueError):
        config.base_path(CarFilters(model="cx-30"))


# --- needs_option_resolution ------------------------------------------------

@pytest.mark.parametrize(
    "filters, expected",
    [
        (CarFilters(price_max=25000), False),
        (CarFilters(mileage_min=1000), False),
        (CarFilters(year_min=2018), True),
        (CarFilters(engine_size_max="2,0L"), True),
    ],
)
def test_needs_option_resolution(filters, expected):
    assert config.needs_option_resolution(filters) is expected


# --- build_search_url: raw params -------------------------------------------

def _query(url: str) -> dict:
    return parse_qs(urlparse(url).query)


def test_build_url_raw_price_and_mileage():
    f = CarFilters(make="mazda", model="cx-30", price_min=5000, price_max=25000,
                   mileage_min=10000, mileage_max=80000)
    q = _query(config.build_search_url(f))
    assert q["price_min"] == ["5000"]
    assert q["price_max"] == ["25000"]
    assert q["attrs__mileage_min"] == ["10000"]
    assert q["attrs__mileage_max"] == ["80000"]


def test_build_url_enumerations_mapped_to_codes():
    f = CarFilters(gearbox="Automatic", fuel_type="Petrol", drive="Front (FWD)", doors="4 - 5 doors")
    q = _query(config.build_search_url(f))
    assert q["attrs__gearbox"] == ["1"]
    assert q["attrs__fuel-type"] == ["7"]
    assert q["attrs__drive"] == ["20"]
    assert q["attrs__doors"] == ["20"]  # "4 - 5 doors"


def test_build_url_enumeration_is_case_insensitive():
    q = _query(config.build_search_url(CarFilters(fuel_type="petrol")))
    assert q["attrs__fuel-type"] == ["7"]


def test_build_url_unknown_enumeration_raises():
    with pytest.raises(ValueError):
        config.build_search_url(CarFilters(gearbox="Rocket"))


def test_build_url_multiselect_repeats_keys():
    f = CarFilters(body_type=[1, 3], seats=[2, 5])
    q = _query(config.build_search_url(f))
    assert q["attrs__body-type"] == ["1", "3"]
    assert q["attrs__seats"] == ["2", "5"]


def test_build_url_omits_unset_filters():
    q = _query(config.build_search_url(CarFilters(make="mazda")))
    assert q == {}


# --- build_search_url: code-resolved params ---------------------------------

def test_build_url_year_uses_resolved_code():
    f = CarFilters(year_min=2018, year_max=2024)
    url = config.build_search_url(f, year_codes={2018: "69", 2024: "78"})
    q = _query(url)
    assert q["attrs__year_min"] == ["69"]
    assert q["attrs__year_max"] == ["78"]


def test_build_url_year_without_codes_raises():
    with pytest.raises(ValueError):
        config.build_search_url(CarFilters(year_min=2018))


def test_build_url_engine_size_uses_resolved_code():
    f = CarFilters(engine_size_max="2,0L")
    url = config.build_search_url(f, engine_codes={"2,0l": "20"})
    assert _query(url)["attrs__engine-size_max"] == ["20"]


def test_build_url_full_example_matches_user_request():
    # Motors > Cars > Mazda > CX-30, 2018+, <= 25000 EUR
    f = CarFilters(make="mazda", model="cx-30", year_min=2018, price_max=25000)
    url = config.build_search_url(f, year_codes={2018: "69"})
    assert url.startswith(
        "https://www.bazaraki.com/car-motorbikes-boats-and-parts/cars-trucks-and-vans/mazda/cx-30/?"
    )
    q = _query(url)
    assert q["price_max"] == ["25000"]
    assert q["attrs__year_min"] == ["69"]