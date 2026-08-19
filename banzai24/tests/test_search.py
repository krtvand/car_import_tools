"""Loading a saved search from its TOML file.

The thing worth guarding here is that a **misspelled key is an error**. A search
definition that quietly ignored ``millage_end`` would run without its mileage
bound, fetch the wrong cars, and produce a report that renders perfectly and is
about the wrong thing — the exact failure mode this codebase is written against.
"""
from __future__ import annotations

import pytest

from banzai24 import search
from banzai24.search import SearchDefinitionError

CX30 = """
[site]
make = "MAZDA"
model = "CX-30"
year_start = 2023
mileage_end = 55000
grade = ["4", "4.5", "5"]

[api]
body_model_code = ["DMEJ3P"]

[sheet]
drivetrain = "4WD"
no_damage_codes = ["W", "X"]
"""


def _write(tmp_path, name="mazda-cx30", text=CX30):
    (tmp_path / f"{name}.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_the_three_sections_land_on_the_three_objects(tmp_path):
    definition = search.load("mazda-cx30", _write(tmp_path))
    assert definition.filters.model == "CX-30"
    assert definition.filters.grade_origin == ("4", "4.5", "5")
    assert definition.lot_filters.body_model_code == ("DMEJ3P",)
    assert definition.requirements.drivetrain == "4WD"
    assert definition.requirements.no_damage_codes == ("W", "X")


def test_an_absent_key_is_an_absent_bound(tmp_path):
    """TOML has no null, and does not need one — the omission is the statement."""
    definition = search.load("mazda-cx30", _write(tmp_path))
    assert definition.filters.year_end is None
    assert definition.filters.engine_capacity_start is None


def test_a_misspelled_key_is_an_error_naming_the_ones_that_exist(tmp_path):
    """Never a shrug. Loading this as "no mileage bound" is a search silently
    running without the filter you thought you wrote."""
    tmp_path = _write(tmp_path, text='[site]\nmake = "MAZDA"\nmillage_end = 55000\n')
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "millage_end" in str(exc.value)
    assert "mileage_end" in str(exc.value)


def test_an_unknown_section_is_an_error(tmp_path):
    tmp_path = _write(tmp_path, text='[site]\nmake = "MAZDA"\n[sheets]\nx = 1\n')
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "sheets" in str(exc.value)


def test_a_bare_string_where_a_list_belongs_is_an_error(tmp_path):
    """Silently iterating it into characters would be a filter matching nothing —
    ``"W"`` would become ``("W",)`` by luck, and ``"WX"`` would become two rules
    that happen to be right, and ``"DMEJ3P"`` a chassis filter matching every
    car with a D in its code."""
    tmp_path = _write(tmp_path, text='[site]\nmake = "MAZDA"\n[api]\nbody_model_code = "DMEJ3P"\n')
    with pytest.raises(SearchDefinitionError) as exc:
        search.load("mazda-cx30", tmp_path)
    assert "must be a list" in str(exc.value)


def test_make_is_required(tmp_path):
    tmp_path = _write(tmp_path, text='[site]\nmodel = "CX-30"\n')
    with pytest.raises(SearchDefinitionError):
        search.load("mazda-cx30", tmp_path)


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


def test_a_run_is_judged_by_the_current_file_not_the_copy_it_saved(tmp_path):
    """The whole reason the run stores a *name*: re-tune a requirement and
    re-render, and this morning is re-judged for nothing."""
    _write(tmp_path)
    payload = {"search": {"name": "mazda-cx30", "site": {"make": "NISSAN"},
                          "api": {}, "sheet": {}}}
    definition, problem = search.for_run(payload)
    assert definition.filters.make == "MAZDA"    # the file, not the stored copy
    assert problem is None


def test_a_deleted_definition_falls_back_to_the_runs_own_copy_and_says_so(tmp_path):
    """Renaming a search must not make an old report silently unjudged."""
    payload = {"search": {"name": "gone", "site": {"make": "MAZDA"},
                          "api": {}, "sheet": {"no_damage_codes": ["W"]}}}
    definition, problem = search.for_run(payload)
    assert definition.requirements.no_damage_codes == ("W",)
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
