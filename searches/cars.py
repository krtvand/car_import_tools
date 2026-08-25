"""The cars this operation buys, named once.

A search says ``car = "mazda-cx30"`` and nothing else about what the car is
called. Every parser then names that car in its own vocabulary — banzai24 wants
``MAZDA`` / ``CX-30`` in a URL path, bazaraki wants the slugs ``toyota`` /
``toyota-rav4``, and neither spelling is derivable from the other. Those tables
live in the parsers (``banzai24/cars.py``, ``bazaraki/cars.py``), because a URL
slug is a fact about one website and belongs beside the code that hit the 404
that proved it.

What is left here is the part that is true of the car rather than of a site: a
stable key, and the make and model as a person says them. The make and model are
also what joins the two databases — ``bazaraki`` stores ``Mazda`` / ``CX-30``
and banzai24 stores ``MAZDA`` / ``CX-30``, and both sides fold case and
punctuation away before comparing, so one spelling here serves both.

Adding a car is three edits: an entry here, a slug in each parser that has to
fetch it. Missing either slug is caught when that parser runs, not here — this
module cannot ask a website what it calls something.
"""
from __future__ import annotations

from dataclasses import dataclass


class UnknownCar(KeyError):
    """A search names a car with no entry here."""


@dataclass(frozen=True)
class Car:
    """One car this operation buys, in the language a person uses for it."""

    key: str
    make: str
    model: str

    def __str__(self) -> str:
        return f"{self.make} {self.model}"


CARS: dict[str, Car] = {
    car.key: car
    for car in (
        Car(key="mazda-cx30", make="Mazda", model="CX-30"),
        Car(key="mazda-cx5", make="Mazda", model="CX-5"),
        Car(key="mazda-3", make="Mazda", model="3"),
        Car(key="toyota-rav4", make="Toyota", model="RAV4"),
    )
}


def get(key: str) -> Car:
    """The car for a key, or :class:`UnknownCar` naming the ones that exist."""
    try:
        return CARS[key]
    except KeyError:
        known = ", ".join(sorted(CARS)) or "none"
        raise UnknownCar(f"unknown car {key!r}. Known cars: {known}") from None
