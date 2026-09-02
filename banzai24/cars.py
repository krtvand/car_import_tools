"""What banzai24 calls each car this operation buys.

banzai24's UI URL carries slugs — ``/MAZDA/CX-30/transmissions-auto`` — and the
spelling is the site's, not ours: the make is upper-case, and the model is
whatever the site's own catalogue says. Neither is derivable from the make and
model a person uses, so they are written down rather than computed, one entry
per car.

They live here, not in the .toml, because they are a fact about *this website*.
A search file names a car (``car = "mazda-cx30"``); how banzai24 spells it is
this module's business, the same way the URL slugs are ``bazaraki/cars.py``'s.
"""
from __future__ import annotations

from cars.definitions import Car


class UnknownCar(KeyError):
    """No banzai24 spelling for a car a search names."""


# car key -> (make slug, model slug), exactly as they appear in a banzai24 URL.
SLUGS: dict[str, tuple[str, str]] = {
    "mazda-cx30": ("MAZDA", "CX-30"),
    "mazda-cx5": ("MAZDA", "CX-5"),
    "mazda-3": ("MAZDA", "3"),
    "toyota-rav4": ("TOYOTA", "RAV4"),
    "toyota-harrier": ("TOYOTA", "HARRIER"),
}


def slugs(car: Car) -> tuple[str, str]:
    """``(make, model)`` as banzai24 spells them, or a raisable complaint.

    Raised rather than guessed: a made-up slug fetches a 404 or, worse, some
    other car's page, and a run built on that would look like a real morning.
    """
    try:
        return SLUGS[car.key]
    except KeyError:
        known = ", ".join(sorted(SLUGS)) or "none"
        raise UnknownCar(
            f"banzai24 has no slugs for car {car.key!r} — add it to "
            f"banzai24/cars.py. Known: {known}"
        ) from None
