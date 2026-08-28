"""Pure parsing helpers for bazaraki car pages.

The site renders from an embedded JSON payload (see `payload.py`), so these
helpers read that payload rather than the generated DOM; the soup is only the
envelope it arrives in. Kept free of network/Crawlee dependencies so they can
be unit-tested against saved page fragments.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from . import payload

BASE_URL = "https://www.bazaraki.com"

# Maps the label shown in the detail-page features list to a model field
# plus an optional converter.
_CHAR_MAP: dict[str, tuple[str, callable]] = {
    "year": ("year", lambda v: _first_int(v)),
    "mileage (in km)": ("mileage_km", lambda v: _first_int(v)),
    "fuel type": ("fuel_type", str.strip),
    "gearbox": ("gearbox", str.strip),
    "body type": ("body_type", str.strip),
    "engine size": ("engine_size", str.strip),
    "power": ("power_hp", lambda v: _first_int(v)),
    "colour": ("colour", str.strip),
    "doors": ("doors", str.strip),
    "seats": ("seats", lambda v: _first_int(v)),
    "drive": ("drive", str.strip),
    "mot till": ("mot_till", str.strip),
    "availability": ("availability", str.strip),
    "extras": ("extras", str.strip),
}


def _first_int(text: str) -> int | None:
    """Pull the first integer out of free text like '7500 km' or '631 hp'."""
    digits = re.sub(r"[\s.,]", "", text)
    match = re.search(r"\d+", digits)
    return int(match.group()) if match else None


# Known bazaraki car makes. Multi-word makes are spelled out so the make/model
# split of a listing title (e.g. "Mercedes-Benz C-Class 2,0L 2024") keeps the
# make whole instead of breaking on the first space. Longest match wins.
CAR_MAKES: tuple[str, ...] = (
    "Abarth", "Alfa Romeo", "Aston Martin", "Audi", "Bentley", "BMW", "Bugatti",
    "Buick", "BYD", "Cadillac", "Chevrolet", "Chrysler", "Citroen", "Cupra",
    "Dacia", "Daewoo", "Daihatsu", "Dodge", "DS", "Ferrari", "Fiat", "Ford",
    "Genesis", "GMC", "Great Wall", "Haval", "Honda", "Hummer", "Hyundai",
    "Infiniti", "Isuzu", "Jaguar", "Jeep", "Kia", "Lada", "Lamborghini",
    "Lancia", "Land Rover", "Lexus", "Lincoln", "Lotus", "Maserati", "Maybach",
    "Mazda", "McLaren", "Mercedes-Benz", "MG", "Mini", "Mitsubishi", "Nissan",
    "Opel", "Peugeot", "Polestar", "Pontiac", "Porsche", "RAM", "Renault",
    "Rolls-Royce", "Rover", "Saab", "Seat", "Skoda", "Smart", "SsangYong",
    "Subaru", "Suzuki", "Tesla", "Toyota", "Vauxhall", "Volkswagen", "Volvo",
    "Zeekr",
)
_MAKES_BY_LENGTH = sorted(CAR_MAKES, key=len, reverse=True)


def _is_engine_or_year(token: str) -> bool:
    """True for the trailing title tokens that describe the car, not the model.

    Covers the year ("2024"), engine size ("2,0L", "3L") and the "Electric"
    marker that stands in for engine size on EV listings.
    """
    return bool(
        re.fullmatch(r"\d{4}", token)
        or re.fullmatch(r"\d+[.,]?\d*[lL]", token)
        or token.lower() == "electric"
    )


def split_make_model(title: str) -> tuple[str | None, str | None]:
    """Split a listing title into ``(make, model)``.

    Titles read "<Make> <Model> <engine>L <year>", e.g.
    "Mercedes-Benz C-Class 2,0L 2024". The make is matched against
    ``CAR_MAKES`` (longest first, so multi-word makes stay whole); the model is
    whatever remains once the trailing engine-size/year tokens are stripped. An
    unrecognised make falls back to the first whitespace token.
    """
    title = title.strip()
    if not title:
        return None, None

    lower = title.lower()
    make: str | None = None
    rest = title
    for candidate in _MAKES_BY_LENGTH:
        c = candidate.lower()
        if lower == c or lower.startswith(c + " "):
            make = candidate
            rest = title[len(candidate):].strip()
            break
    if make is None:
        make, _, rest = title.partition(" ")
        rest = rest.strip()

    tokens = rest.split()
    while tokens and _is_engine_or_year(tokens[-1]):
        tokens.pop()
    model = " ".join(tokens)
    return make or None, model or None


# Transmission values that may appear (unlabelled) among the inline list-card
# features; anything else in that slot is treated as the fuel type.
_GEARBOX_VALUES = {"automatic", "manual", "semi-automatic", "tiptronic", "cvt"}


def _ad_id_from_url(url: str) -> int | None:
    match = re.search(r"/adv/(\d+)", url)
    return int(match.group(1)) if match else None


def _abs_url(href: str) -> str:
    """Absolute URL with the trailing ``?p=N`` pagination marker stripped."""
    path = href.split("?", 1)[0]
    return path if path.startswith("http") else BASE_URL + path


def listing_payload(soup: BeautifulSoup) -> dict:
    """The search-results object embedded in a category page."""
    return payload.find(soup, "adverts", "next_page", "count") or {}


def parse_cards(soup: BeautifulSoup) -> list[dict]:
    """Extract base fields from every listing on a category page.

    Reads the ``adverts`` list of the page payload, which carries price,
    mileage, gearbox, fuel, location and date for each advert.
    """
    results: list[dict] = []
    for advert in listing_payload(soup).get("adverts", []):
        ad_id, url = advert.get("id"), advert.get("url")
        if not ad_id or not url:
            continue

        title = advert.get("title") or ""
        make, model = split_make_model(title)
        price = _first_int(advert.get("price_without_currency") or "")
        record = {
            "ad_id": int(ad_id),
            "title": title,
            "make": make,
            "model": model,
            "url": _abs_url(url),
            "price": float(price) if price else None,
            "currency": advert.get("currency") or None,
            "image_url": advert.get("first_thumb") or None,
            "photo_count": advert.get("img_count") or None,
            "location": advert.get("location") or None,
            "posted_raw": advert.get("published") or None,
        }

        # Inline, unlabelled features: "13000 km" · "Automatic" · "Hybrid Diesel".
        for feature in advert.get("features", []):
            text = (feature.get("text") or "").strip()
            if not text:
                continue
            if "km" in text.lower():
                record["mileage_km"] = _first_int(text)
            elif text.lower() in _GEARBOX_VALUES:
                record["gearbox"] = text
            else:
                record["fuel_type"] = text

        results.append(record)
    return results


def has_next_page(soup: BeautifulSoup) -> bool:
    """True if the search has results beyond the page this soup came from.

    ``next_page`` is the site's own cursor for the following page and is null
    on the last one, so it answers this without having to count adverts.
    """
    return bool(listing_payload(soup).get("next_page"))


def with_page(url: str, page: int) -> str:
    """Return ``url`` with its ``page`` query param set to ``page``.

    Preserves all other query params, including repeated keys (multi-select
    filters such as ``attrs__body-type``), so pagination keeps the filters.
    """
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    query.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(query)))


def _filter_choices(soup: BeautifulSoup, slug: str) -> list[tuple[str, str]]:
    """``(code, label)`` pairs offered by the ``slug`` filter on this page."""
    for value in payload.row_values(payload.flight_stream(soup)):
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("slug") == slug:
                return [
                    (str(code), str(label))
                    for code, label in item.get("choices", [])
                ]
    return []


def parse_year_codes(soup: BeautifulSoup) -> dict[int, str]:
    """Map calendar year -> site option code from the year filter."""
    return {
        int(label): code
        for code, label in _filter_choices(soup, "attrs__year")
        if label.isdigit()
    }


def parse_engine_codes(soup: BeautifulSoup) -> dict[str, str]:
    """Map engine-size label (lowercased) -> site option code."""
    return {
        label.lower(): code
        for code, label in _filter_choices(soup, "attrs__engine-size")
    }


def parse_detail(soup: BeautifulSoup) -> dict:
    """Extract enrichment fields from an advert detail page."""
    advert = payload.find(soup, "id", "features", "counters", "user", "gallery")
    if advert is None:
        return {}

    data: dict = {}
    for feature in advert.get("features", []):
        mapping = _CHAR_MAP.get((feature.get("name") or "").strip().lower())
        if mapping:
            field, convert = mapping
            data[field] = convert((feature.get("value") or "").strip())

    location = (advert.get("location") or {}).get("name")
    if location:
        data["location"] = location

    # The seller's own words. Kept because they are the only place a Cyprus
    # advert names the car's trim — "(G package)", "Hybrid X 2wd", "Adventure
    # OFF ROAD PACKAGE II" — which the site's own structured fields never carry.
    description = (advert.get("description") or "").strip()
    if description:
        data["description"] = description

    published = (advert.get("counters") or {}).get("published")
    if published:
        data["posted_raw"] = published  # e.g. "Yesterday", "19.06.2026 09:56"

    seller_type = _seller_type(advert.get("user"))
    if seller_type:
        data["seller_type"] = seller_type

    return data


def _seller_type(user: dict | None) -> str | None:
    """Classify the seller as 'dealer' or 'private'.

    The payload states it outright: business accounts (and companies, which the
    site flags separately) are the dealers, everyone else is a private seller.
    """
    if not user:
        return None
    is_dealer = bool(user.get("is_business_account") or user.get("is_company"))
    return "dealer" if is_dealer else "private"
