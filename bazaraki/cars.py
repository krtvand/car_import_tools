"""What bazaraki calls each car this operation buys.

bazaraki puts the make and model in the URL path —
``/car-motorbikes-boats-and-parts/cars-trucks-and-vans/mazda/cx-30/`` — and the
slugs are not a rule you can apply. **bazaraki prefixes the make on some
models**: the RAV4 lives at ``/toyota/toyota-rav4/``, and both ``/toyota/rav4/``
and ``/toyota/rav-4/`` return 404. That was verified live, and it is exactly the
kind of thing that has to be written down rather than derived.

They live here, not in the .toml, because they are a fact about *this website*.
A search file names a car (``car = "toyota-rav4"``); how bazaraki spells it in a
URL is this module's business.
"""
from __future__ import annotations

from searches.cars import Car


class UnknownCar(KeyError):
    """No bazaraki slug for a car a search names."""


# car key -> (make slug, model slug) in the category URL.
SLUGS: dict[str, tuple[str, str]] = {
    "mazda-cx30": ("mazda", "cx-30"),
    "mazda-cx5": ("mazda", "cx-5"),
    "mazda-3": ("mazda", "3"),
    # Not "rav4" and not "rav-4" — both 404. bazaraki prefixes the make here.
    "toyota-rav4": ("toyota", "toyota-rav4"),
}


def slugs(car: Car) -> tuple[str, str]:
    """``(make, model)`` as bazaraki spells them in a URL.

    Raised rather than guessed. A wrong slug 404s, and a scrape that quietly
    found nothing is indistinguishable on the dashboard from a car nobody in
    Cyprus is selling — which is the good news this whole panel exists to
    disprove.
    """
    try:
        return SLUGS[car.key]
    except KeyError:
        known = ", ".join(sorted(SLUGS)) or "none"
        raise UnknownCar(
            f"bazaraki has no slugs for car {car.key!r} — add it to "
            f"bazaraki/cars.py. Known: {known}"
        ) from None
