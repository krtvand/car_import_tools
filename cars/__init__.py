"""What is true of a car, as opposed to of a website, a search or a month.

Four kinds of fact about the cars this operation buys, in one package that
imports nothing else in the repo:

* :mod:`cars.definitions` — the key a search names a car by, and the make and
  model as a person says them.
* :mod:`cars.specs` — the manufacturer's figures per generation: dimensions,
  CO₂, Euro standard, fuel. Two of those spend money.
* :mod:`cars.trims` — the グレード box on an auction sheet, in Japanese and in
  English.
* ``cars/reference/`` — the prose and the manufacturer PDFs behind the tables:
  how to tell a Harrier's four trims apart, what each is worth in Japan and in
  Cyprus, and where every figure came from.

**This package is the dependency root.** :mod:`searches` imports it,
:mod:`banzai24`, :mod:`bazaraki`, :mod:`price_calculator` and :mod:`dashboard`
import both, and nothing here imports any of them — the same one-way rule
``searches`` has held since ADR-0005, one level further down. The rule that
keeps it honest: nothing that is true of a *website* comes in here. A URL slug
lives with the parser that hit the 404 proving it, and both parsers still keep
their own ``cars.py`` for exactly that.

See ``docs/adr/0008-a-car-is-not-a-price.md`` for why the model specs moved out
of ``price_calculator``.
"""
from .definitions import CARS, Car, UnknownCar, get
from .specs import (
    MODEL_SPECS_PATH,
    ModelSpec,
    ModelSpecError,
    ModelSpecs,
    load_model_specs,
)
from .trims import Trim, TrimReading, TrimTableError

__all__ = [
    "CARS", "Car", "UnknownCar", "get",
    "MODEL_SPECS_PATH", "ModelSpec", "ModelSpecError", "ModelSpecs",
    "load_model_specs",
    "Trim", "TrimReading", "TrimTableError",
]
