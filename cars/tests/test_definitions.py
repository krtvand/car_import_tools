"""The car registry — small, but three modules key off it."""
from __future__ import annotations

import pytest

from cars import definitions


def test_a_car_is_keyed_by_the_key_it_carries():
    for key, car in definitions.CARS.items():
        assert car.key == key


def test_a_car_says_its_make_and_model_the_way_a_person_does():
    assert str(definitions.get("toyota-harrier")) == "Toyota Harrier"


def test_an_unknown_key_names_the_cars_that_exist():
    """The error is the documentation: it is read at a prompt, not in a file."""
    with pytest.raises(definitions.UnknownCar, match="toyota-harrier"):
        definitions.get("toyota-hilux")
