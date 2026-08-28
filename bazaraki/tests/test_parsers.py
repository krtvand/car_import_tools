"""Tests for the pure HTML-parsing helpers."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from bazaraki import parsers


# --- _first_int -------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("7500 km", 7500),
        ("631 hp", 631),
        ("38.500", 38500),      # European thousands separator
        ("1,250,000", 1250000),
        ("2023", 2023),
        ("In stock", None),
        ("", None),
    ],
)
def test_first_int(text, expected):
    assert parsers._first_int(text) == expected


# --- split_make_model -------------------------------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        ("Mercedes-Benz C-Class 2,0L 2024", ("Mercedes-Benz", "C-Class")),
        ("BMW 4 Series 3,0L 2016", ("BMW", "4 Series")),
        ("Land Rover Range Rover 3,0L 2020", ("Land Rover", "Range Rover")),
        ("Tesla Model 3 Electric 2022", ("Tesla", "Model 3")),
        ("Toyota Yaris 2019", ("Toyota", "Yaris")),          # no engine token
        ("Lada Niva", ("Lada", "Niva")),                     # no engine/year
        ("Wartburg 353 1,0L 1985", ("Wartburg", "353")),     # unknown make
        ("", (None, None)),
    ],
)
def test_split_make_model(title, expected):
    assert parsers.split_make_model(title) == expected


# --- _ad_id_from_url / _abs_url ---------------------------------------------

def test_ad_id_from_url():
    assert parsers._ad_id_from_url("/adv/6030738_lamborghini/") == 6030738
    assert parsers._ad_id_from_url("/adv/6030738_x/?p=2") == 6030738
    assert parsers._ad_id_from_url("/no-advert-here/") is None


def test_abs_url_strips_pagination_query():
    assert parsers._abs_url("/adv/123_x/?p=2") == "https://www.bazaraki.com/adv/123_x/"
    assert parsers._abs_url("/adv/123_x/") == "https://www.bazaraki.com/adv/123_x/"


def test_abs_url_keeps_absolute():
    assert parsers._abs_url("https://www.bazaraki.com/adv/9/") == "https://www.bazaraki.com/adv/9/"


# --- parse_cards ------------------------------------------------------------

def test_parse_cards_skips_entries_without_an_advert(list_soup):
    # Three entries in the fixture payload; the last has no id/url.
    assert len(parsers.parse_cards(list_soup)) == 2


def test_parse_cards_full_card(list_soup):
    card = parsers.parse_cards(list_soup)[0]
    assert card == {
        "ad_id": 6350404,
        "title": "Mercedes-Benz C-Class 2,0L 2024",
        "make": "Mercedes-Benz",
        "model": "C-Class",
        "url": "https://www.bazaraki.com/adv/6350404_mercedes-benz-c-class-2-0l-2024/",
        "price": 38500.0,
        "currency": "EUR",
        "image_url": "https://cdn1.bazaraki.com/media/a.webp",
        "photo_count": 10,
        "location": "Limassol, Kato Polemidia",
        "posted_raw": "today",
        "mileage_km": 13000,
        "gearbox": "Automatic",
        "fuel_type": "Hybrid Diesel",
    }


def test_parse_cards_splits_make_and_model_from_title(list_soup):
    cards = parsers.parse_cards(list_soup)
    assert (cards[0]["make"], cards[0]["model"]) == ("Mercedes-Benz", "C-Class")
    assert (cards[1]["make"], cards[1]["model"]) == ("BMW", "4 Series")


def test_parse_cards_minimal_card_has_no_optional_fields(list_soup):
    card = parsers.parse_cards(list_soup)[1]
    assert card["ad_id"] == 6564331
    assert card["price"] == 15000.0
    assert card["currency"] == "EUR"
    # Always-present card fields fall back to None when the payload sends "".
    assert card["location"] is None
    assert card["posted_raw"] is None
    assert card["image_url"] is None
    assert card["photo_count"] is None
    # Feature-derived fields are omitted entirely when no feature matched.
    for missing in ("mileage_km", "gearbox", "fuel_type"):
        assert missing not in card


def test_parse_cards_returns_nothing_without_a_payload():
    soup = BeautifulSoup("<html><body>no payload here</body></html>", "html.parser")
    assert parsers.parse_cards(soup) == []


# --- parse_detail -----------------------------------------------------------

def test_parse_detail_maps_all_known_characteristics(detail_soup):
    data = parsers.parse_detail(detail_soup)
    assert data["year"] == 2023
    assert data["mileage_km"] == 7500
    assert data["power_hp"] == 631
    assert data["seats"] == 2
    assert data["fuel_type"] == "Petrol"
    assert data["gearbox"] == "Automatic"
    assert data["body_type"] == "Coupe"
    assert data["engine_size"] == "5,2L"
    assert data["colour"] == "Grey"
    assert data["doors"] == "2 - 3 doors"
    assert data["drive"] == "4WD, AWD"
    assert data["mot_till"] == "12/2027"
    assert data["availability"] == "In stock"


def test_parse_detail_location_and_date(detail_soup):
    data = parsers.parse_detail(detail_soup)
    assert data["location"] == "Limassol, Agios Tychon"
    assert data["posted_raw"] == "19.06.2026 09:56"


def test_parse_detail_ignores_unmapped_features(detail_soup):
    data = parsers.parse_detail(detail_soup)
    assert all(v != "should be skipped" for v in data.values())


def test_parse_detail_keeps_the_sellers_own_words(detail_soup):
    """The only field that ever names a trim — "(G package)", "Hybrid X 2wd" —
    which is what a [competitors] exclusion reads."""
    data = parsers.parse_detail(detail_soup)
    assert data["description"].startswith("Lamborghini Huracan EVO RWD")


def test_parse_detail_returns_nothing_without_a_payload():
    soup = BeautifulSoup("<html><body>no payload here</body></html>", "html.parser")
    assert parsers.parse_detail(soup) == {}


# --- seller_type ------------------------------------------------------------

@pytest.mark.parametrize(
    "user, expected",
    [
        ({"is_business_account": True, "is_company": True}, "dealer"),
        ({"is_business_account": True, "is_company": False}, "dealer"),
        # A company posting from a personal-looking account is still a dealer.
        ({"is_business_account": False, "is_company": True}, "dealer"),
        ({"is_business_account": False, "is_company": False}, "private"),
        # Verification alone doesn't make a dealer.
        ({"is_verified": True}, "private"),
        (None, None),
    ],
)
def test_seller_type(user, expected):
    assert parsers._seller_type(user) == expected


def test_parse_detail_includes_seller_type(detail_soup):
    assert parsers.parse_detail(detail_soup)["seller_type"] == "dealer"


# --- pagination helpers -----------------------------------------------------

def test_has_next_page(list_soup, last_list_soup):
    assert parsers.has_next_page(list_soup) is True
    # The last page's next-page cursor is null.
    assert parsers.has_next_page(last_list_soup) is False


def test_has_next_page_without_a_payload():
    soup = BeautifulSoup("<html><body>no payload here</body></html>", "html.parser")
    assert parsers.has_next_page(soup) is False


def test_with_page_adds_param():
    url = "https://www.bazaraki.com/cars/mazda/cx-30/?price_max=25000"
    assert parsers.with_page(url, 2) == (
        "https://www.bazaraki.com/cars/mazda/cx-30/?price_max=25000&page=2"
    )


def test_with_page_replaces_existing_param():
    url = "https://www.bazaraki.com/cars/?price_max=25000&page=2"
    out = parsers.with_page(url, 3)
    assert "page=3" in out
    assert "page=2" not in out


def test_with_page_preserves_repeated_keys():
    url = "https://www.bazaraki.com/cars/?attrs__body-type=1&attrs__body-type=3&page=1"
    out = parsers.with_page(url, 2)
    assert out.count("attrs__body-type=") == 2
    assert out.endswith("page=2")


# --- option-code resolution -------------------------------------------------

def test_parse_year_codes(filter_form_soup):
    codes = parsers.parse_year_codes(filter_form_soup)
    assert codes[2026] == "80"
    assert codes[2018] == "69"
    # "Older" is non-numeric and must be skipped.
    assert all(isinstance(k, int) for k in codes)


def test_parse_engine_codes(filter_form_soup):
    codes = parsers.parse_engine_codes(filter_form_soup)
    assert codes["2,0l"] == "17"
    assert codes["electric"] == "80"