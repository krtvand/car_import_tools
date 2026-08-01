"""Pure HTML-parsing helpers for bazaraki car pages.

Kept free of network/Crawlee dependencies so they can be unit-tested against
saved HTML fragments.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://www.bazaraki.com"

# Maps the label shown in the detail-page characteristics list to a model field
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


def parse_cards(soup: BeautifulSoup) -> list[dict]:
    """Extract base fields from every listing card on a category page.

    Uses the list-view container (``div.advert.js-item-listing``), which is
    present on every page and carries price, mileage, gearbox, fuel, location
    and date inline.
    """
    results: list[dict] = []
    for card in soup.select("div.advert.js-item-listing"):
        link = card.select_one("a.advert__content-title")
        if link is None or not link.get("href"):
            continue
        ad_id = _first_int(card.get("data-id", "")) or _ad_id_from_url(link["href"])
        if ad_id is None:
            continue

        price_el = card.select_one("a.advert__content-price")
        place_el = card.select_one(".advert__content-place")
        date_el = card.select_one(".advert__content-date")
        photo_el = card.select_one(".advert__body-property._photo span[data-count]")
        first_slide = card.select_one("a.swiper-slide[data-background]")

        title = link.get_text(strip=True)
        make, model = split_make_model(title)
        record = {
            "ad_id": ad_id,
            "title": title,
            "make": make,
            "model": model,
            "url": _abs_url(link["href"]),
            "price": float(_first_int(price_el.get_text())) if price_el and _first_int(price_el.get_text()) else None,
            "currency": "EUR" if price_el and "€" in price_el.get_text() else None,
            "image_url": first_slide.get("data-background") if first_slide else None,
            "photo_count": _first_int(photo_el.get("data-count", "")) if photo_el else None,
            "location": place_el.get_text(" ", strip=True) if place_el else None,
            "posted_raw": date_el.get_text(strip=True) if date_el else None,
        }

        # Inline, unlabelled features: "13000 km" · "Automatic" · "Hybrid Diesel".
        for feat in card.select(".advert__content-feature > div"):
            text = feat.get_text(" ", strip=True)
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


def next_page_url(soup: BeautifulSoup, current_page: int) -> str | None:
    """Return the absolute URL of the next page, or None if there isn't one."""
    target = str(current_page + 1)
    for a in soup.select("a.page-number"):
        if a.get("data-page") == target and a.get("href"):
            return BASE_URL + a["href"]
    return None


def has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    """True if a pagination link to ``current_page + 1`` exists."""
    target = str(current_page + 1)
    return any(a.get("data-page") == target for a in soup.select("a.page-number"))


def with_page(url: str, page: int) -> str:
    """Return ``url`` with its ``page`` query param set to ``page``.

    Preserves all other query params, including repeated keys (multi-select
    filters such as ``attrs__body-type``), so pagination keeps the filters.
    """
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    query.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(query)))


def parse_year_codes(soup: BeautifulSoup) -> dict[int, str]:
    """Map calendar year -> site option code from the year filter <select>."""
    codes: dict[int, str] = {}
    sel = soup.select_one("select[name='attrs__year_min']")
    if sel:
        for opt in sel.select("option"):
            value = opt.get("value")
            text = opt.get_text(strip=True)
            if value and text.isdigit():
                codes[int(text)] = value
    return codes


def parse_engine_codes(soup: BeautifulSoup) -> dict[str, str]:
    """Map engine-size label (lowercased) -> site option code."""
    codes: dict[str, str] = {}
    sel = soup.select_one("select[name='attrs__engine-size_min']")
    if sel:
        for opt in sel.select("option"):
            value = opt.get("value")
            text = opt.get_text(strip=True)
            if value:
                codes[text.lower()] = value
    return codes


def parse_detail(soup: BeautifulSoup) -> dict:
    """Extract enrichment fields from an advert detail page."""
    data: dict = {}

    for li in soup.select("ul.chars-column li"):
        text = li.get_text(" ", strip=True)
        if ":" not in text:
            continue
        label, _, value = text.partition(":")
        mapping = _CHAR_MAP.get(label.strip().lower())
        if mapping:
            field, convert = mapping
            data[field] = convert(value.strip())

    address = soup.select_one("[itemprop='address']")
    if address:
        data["location"] = address.get_text(" ", strip=True)

    date_meta = soup.select_one("span.date-meta")
    if date_meta:
        # "Posted: 19.06.2026 09:56" -> "19.06.2026 09:56"
        data["posted_raw"] = date_meta.get_text(" ", strip=True).replace("Posted:", "").strip()

    return data