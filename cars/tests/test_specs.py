"""The model spec table: what loads, what is refused, and the shipped file itself.

The split is deliberate and mirrors the cost book's (ADR 0003). Against the
**shipped file** only invariants are asserted — it parses, every saved search is
covered, the volumes are plausible — so correcting a real dimension stays green.
Against **hand-written tables** the loader's refusals are asserted, so a mis-edit
is caught the morning it is made rather than three reports later.

These tests moved here with the table itself; see
``docs/adr/0008-a-car-is-not-a-price.md``.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cars.specs import (
    MODEL_SPECS_PATH,
    ModelSpecError,
    ModelSpecs,
    load_model_specs,
)

HEADER = ("make,model,year_from,year_to,length_cm,width_cm,height_cm,"
          "co2_gkm,euro_standard,fuel,body_model_code\n")


def write(tmp_path: Path, body: str, preamble: str = "") -> Path:
    path = tmp_path / "model_specs.csv"
    path.write_text(preamble + HEADER + body, encoding="utf-8")
    return path


# --- the shipped file --------------------------------------------------------


def test_the_shipped_specs_cover_every_band_of_every_saved_search():
    """Every car you bid on must be priceable, or the table has a hole in it.

    A missing spec is not an error anywhere — it degrades to a reason on one
    card — so nothing else would notice a car being added to a search without
    its dimensions. Freight is 17% of the CNF price, and it is the half of a
    landed cost that this would silently remove.
    """
    import searches

    specs = ModelSpecs()
    assert specs.available, specs.reason

    for name, search, problem in searches.load_all():
        assert problem is None, f"{name}: {problem}"
        for band in search.bands:
            assert specs.for_car(search.car.make, search.car.model, band.year) is not None, \
                f"no model spec covers {search.car} {band.year} ({name})"


def test_the_shipped_specs_have_plausible_volumes():
    """A transposed digit is the failure mode here, and it is worth ~EUR 150."""
    for spec in load_model_specs(MODEL_SPECS_PATH):
        assert Decimal(8) < spec.volume_m3 < Decimal(20), spec.describe()


def test_the_preamble_above_the_header_is_ignored():
    """The shipped file opens with four lines of prose saying it is unverified."""
    assert load_model_specs(MODEL_SPECS_PATH)
    assert "UNVERIFIED" in MODEL_SPECS_PATH.read_text(encoding="utf-8")


# --- loading -----------------------------------------------------------------


def test_a_row_loads_with_its_optional_columns_blank(tmp_path):
    """All four optional cells are blank-able; only one of them blanks a price.

    An empty ``co2_gkm`` costs the row its landed cost entirely — see
    ``test_a_spec_with_no_co2_prices_nothing_and_says_why`` — but it is still a
    row that *loads*, because one hole must not cost the other rows their prices.
    """
    path = write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,,,\n")
    spec, = load_model_specs(path)
    assert spec.co2_gkm is None
    assert spec.euro_standard is None
    assert spec.fuel is None
    assert spec.body_model_code is None
    assert round(spec.volume_m3, 2) == Decimal("14.27")


def test_the_new_columns_are_read_off_the_row(tmp_path):
    path = write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n")
    spec, = load_model_specs(path)
    assert (spec.co2_gkm, spec.euro_standard, spec.fuel) == (158, "6", "petrol")


def test_the_header_is_matched_folded(tmp_path):
    """A re-export that recases a column must not read every row as blank."""
    path = tmp_path / "model_specs.csv"
    path.write_text(
        "Make,Model,Year From,Year To,Length CM,Width CM,Height CM,CO2 g/km,Euro Standard,Fuel,Body Model Code\n"
        "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n", encoding="utf-8")
    spec, = load_model_specs(path)
    assert spec.make == "MAZDA"


def test_overlapping_year_spans_are_rejected_at_load(tmp_path):
    """Two rows that could both describe one car, caught before a car falls in."""
    path = write(tmp_path,
                 "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n"
                 "MAZDA,CX-5,2024,2028,460.0,187.0,168.0,150,6,petrol,KF5P\n")
    with pytest.raises(ModelSpecError, match="year spans overlap"):
        load_model_specs(path)


def test_adjacent_year_spans_are_fine(tmp_path):
    path = write(tmp_path,
                 "MAZDA,CX-5,2017,2023,457.5,184.5,169.0,158,6,petrol,KFEP\n"
                 "MAZDA,CX-5,2024,2028,460.0,187.0,168.0,150,6,petrol,KF5P\n")
    assert len(load_model_specs(path)) == 2


def test_a_backwards_year_span_is_rejected(tmp_path):
    path = write(tmp_path, "MAZDA,CX-5,2026,2017,457.5,184.5,169.0,,,,\n")
    with pytest.raises(ModelSpecError, match="before"):
        load_model_specs(path)


@pytest.mark.parametrize("body,match", [
    (",CX-5,2017,2026,457.5,184.5,169.0,,,,\n", "make and model are required"),
    ("MAZDA,CX-5,2017,2026,,184.5,169.0,,,,\n", "length_cm is empty"),
    ("MAZDA,CX-5,2017,2026,wide,184.5,169.0,,,,\n", "not a number"),
    ("MAZDA,CX-5,2017,2026,457.5,184.5,169.0,lots,,,\n", "not a whole number"),
])
def test_a_bad_cell_names_the_column(tmp_path, body, match):
    with pytest.raises(ModelSpecError, match=match):
        load_model_specs(write(tmp_path, body))


def test_a_missing_file_costs_the_column_not_the_page(tmp_path):
    """Mirrors BidPricer: a missing input is reported, never raised."""
    specs = ModelSpecs(tmp_path / "absent.csv")
    assert not specs.available
    assert specs.reason == "model specs not loaded"
    assert specs.for_car("MAZDA", "CX-5", 2023) is None


def test_a_malformed_file_attaches_the_parsers_complaint(tmp_path):
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2026,2017,457.5,184.5,169.0,,,,\n"))
    assert not specs.available
    assert "year_to 2017 is before" in specs.reason


# --- lookup ------------------------------------------------------------------


def test_lookup_folds_case_and_punctuation(tmp_path):
    """banzai24 writes MAZDA / CX-30, bazaraki writes Mazda / cx30."""
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-30,2019,2026,439.5,179.5,154.0,,,,\n"))
    assert specs.for_car("mazda", "cx30", 2023) is not None
    assert specs.for_car("Mazda", "CX 30", 2020) is not None


def test_a_year_outside_every_span_is_none_not_the_nearest_row(tmp_path):
    """Freight is 17% of CNF — a borrowed row is wrong by more than any fee here."""
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,,,\n"))
    assert specs.for_car("MAZDA", "CX-5", 2010) is None
    assert specs.for_car("MAZDA", "CX-9", 2023) is None


def test_missing_identifiers_do_not_match_anything(tmp_path):
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,,,\n"))
    assert specs.for_car(None, "CX-5", 2023) is None
    assert specs.for_car("MAZDA", "CX-5", None) is None


