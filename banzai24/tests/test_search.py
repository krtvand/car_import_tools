"""banzai24's adapter over a saved search: the file turned into its own filters.

The file format itself is ``searches/tests/test_definition.py``'s business. What
is guarded here is the translation — that ``[site]``, ``[api]`` and ``[sheet]``
land on the three objects banzai24 actually uses, that the car becomes banzai24's
own spelling of it, and that **the year and mileage bounds come from the bands**
rather than from anything written by hand.

A misspelled key is still an error at this boundary too. A definition that
quietly ignored ``millage_end`` would run without its mileage bound, fetch the
wrong cars, and produce a report that renders perfectly and is about the wrong
thing — the exact failure mode this codebase is written against.
"""
from __future__ import annotations

import pytest

from banzai24 import search
from banzai24.search import SearchDefinitionError

CX30 = """
car = "mazda-cx30"

[site]
transmission = "auto"
grade = ["4", "4.5", "5"]

[api]
body_model_code = ["DMEJ3P"]
exclude_colours = ["black", "blue"]

[sheet]
drivetrain = "4WD"
no_damage_codes = ["W", "X"]

[[band]]
year = 2023
mileage_end = 55000
max_bid_jpy = { private = 1_805_000 }
"""


def _write(tmp_path, name="mazda-cx30", text=CX30):
    (tmp_path / f"{name}.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_the_three_sections_land_on_the_three_objects(tmp_path):
    definition = search.load("mazda-cx30", _write(tmp_path))
    assert definition.filters.grade_origin == ("4", "4.5", "5")
    assert definition.lot_filters.body_model_code == ("DMEJ3P",)
    assert definition.lot_filters.exclude_colours == ("black", "blue")
    assert definition.requirements.drivetrain == "4WD"
    assert definition.requirements.no_damage_codes == ("W", "X")


def test_the_car_becomes_banzai24s_own_spelling_of_it(tmp_path):
    """The .toml names a car; how this site writes it is ``banzai24/cars.py``'s
    business, and the URL slugs are upper-case where a person is not."""
    definition = search.load("mazda-cx30", _write(tmp_path))
    assert (definition.filters.make, definition.filters.model) == ("MAZDA", "CX-30")


def test_the_bounds_are_the_union_of_the_bands(tmp_path):
    """Not read from the file at all. Writing a bound beside the prices it covers
    is how the two drift, which they had already done twice."""
    definition = search.load("mazda-cx30", _write(tmp_path, text=CX30 + """
[[band]]
year = 2024
mileage_start = 55001
mileage_end = 70000
max_bid_jpy = { private = 1_900_000 }
"""))
    assert (definition.filters.year_start, definition.filters.year_end) == (2023, 2024)
    assert (definition.filters.mileage_start, definition.filters.mileage_end) == (0, 70_000)


def test_a_bound_written_in_site_is_an_error_not_an_override(tmp_path):
    """An override would be a bound silently disagreeing with the prices under
    it, which is the drift the merge exists to end."""
    tmp_path = _write(tmp_path, text=CX30.replace(
        "[site]\n", "[site]\nmileage_end = 40000\n"))
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "derived from the bands" in str(exc.value)


def test_an_absent_key_is_an_absent_bound(tmp_path):
    """TOML has no null, and does not need one — the omission is the statement."""
    definition = search.load("mazda-cx30", _write(tmp_path))
    assert definition.filters.engine_capacity_start is None


def test_a_misspelled_key_is_an_error_naming_the_ones_that_exist(tmp_path):
    """Never a shrug. Loading this as "no transmission filter" is a search
    silently running without the filter you thought you wrote."""
    tmp_path = _write(tmp_path, text=CX30.replace(
        'transmission = "auto"', 'transmision = "auto"'))
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "transmision" in str(exc.value)
    assert "transmission" in str(exc.value)


def test_a_bare_string_where_a_list_belongs_is_an_error(tmp_path):
    """Silently iterating it into characters would be a filter matching nothing —
    ``"W"`` would become ``("W",)`` by luck, and ``"WX"`` would become two rules
    that happen to be right, and ``"DMEJ3P"`` a chassis filter matching every
    car with a D in its code."""
    tmp_path = _write(tmp_path, text=CX30.replace(
        'body_model_code = ["DMEJ3P"]', 'body_model_code = "DMEJ3P"'))
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "must be a list" in str(exc.value)


def test_an_unknown_name_lists_what_is_available(tmp_path):
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx31", _write(tmp_path))
    assert "mazda-cx30" in str(exc.value)


def test_broken_toml_names_the_file_rather_than_raising_a_parser_error(tmp_path):
    tmp_path = _write(tmp_path, text="[site\nmake =")
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "mazda-cx30.toml" in str(exc.value)


# --- what a run remembers ----------------------------------------------------


def _provenance(**overrides) -> dict:
    stored = {
        "name": "mazda-cx30", "car": "mazda-cx30",
        "site": {}, "api": {}, "sheet": {},
        "bands": [{"year": 2023, "mileage_start": 0, "mileage_end": 55_000,
                   "max_bid_jpy": {"private": 1_805_000}}],
    }
    return {"search": {**stored, **overrides}}


def test_a_run_is_judged_by_the_current_file_not_the_copy_it_saved(monkeypatch, tmp_path):
    """The whole reason the run stores a *name*: re-tune a requirement — or a max
    bid — and re-render, and this morning is re-judged for nothing. That the bids
    move too is ``docs/adr/0004-bid-prices-are-read-live.md``."""
    import searches.definition as definition_module

    _write(tmp_path)
    monkeypatch.setattr(definition_module, "SEARCH_DIR", tmp_path)

    definition, problem = search.for_run(_provenance(sheet={"drivetrain": "2WD"}))
    assert definition.requirements.drivetrain == "4WD"   # the file, not the copy
    assert definition.bands[0].bid() == 1_805_000
    assert problem is None


def test_a_deleted_definition_falls_back_to_the_runs_own_copy_and_says_so(tmp_path):
    """Renaming a search must not make an old report silently unjudged."""
    definition, problem = search.for_run(
        _provenance(name="gone", sheet={"no_damage_codes": ["W"]}))
    assert definition.requirements.no_damage_codes == ("W",)
    assert definition.bands[0].bid() == 1_805_000
    assert "not the current file" in problem


def test_a_run_that_named_no_search_is_not_judged_at_all(tmp_path):
    """Runs fetched before searches were files. Inventing a verdict for them
    would be the report claiming to know something it does not."""
    definition, problem = search.for_run({"filters": {"make": "MAZDA"}})
    assert definition is None
    assert problem is None


def test_the_provenance_round_trips(tmp_path):
    original = search.load("mazda-cx30", _write(tmp_path))
    restored = search.from_provenance({"search": original.to_payload()})
    assert restored.filters == original.filters
    assert restored.lot_filters == original.lot_filters
    assert restored.requirements == original.requirements
    assert restored.bands == original.bands
