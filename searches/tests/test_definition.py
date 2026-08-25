"""The search file format: bands, competitor bounds, and refusing to load.

Everything guarded here is a **wrong file that would otherwise still run**. A
band that overlaps another one silently shadows a price; competitor bounds
narrower than their own band silently shorten a list, and a short list reads as
good news; a bound written in ``[site]`` silently disagrees with the prices
underneath it. None of those raise at the point of use — they just produce a
plausible page — so they have to be caught at load or not at all.
"""
from __future__ import annotations

import pytest

from searches import definition
from searches.definition import SearchDefinitionError

CX30 = {
    "car": "mazda-cx30",
    "site": {"transmission": "auto"},
    "band": [
        {"year": 2023, "mileage_start": 0, "mileage_end": 50_000,
         "max_bid_jpy": {"private": 1_805_000},
         "competitors": {"year_start": 2019, "mileage_end": 120_000}},
        {"year": 2023, "mileage_start": 50_001, "mileage_end": 60_000,
         "max_bid_jpy": {"private": 1_705_000},
         "competitors": {"year_start": 2019, "mileage_end": 120_000}},
    ],
}


def _parse(**overrides):
    return definition.parse({**CX30, **overrides}, name="mazda-cx30")


# --- what a search is made of ------------------------------------------------


def test_the_bounds_are_the_union_of_the_bands():
    """Never written by hand. Two searches had already drifted from their price
    table when this replaced it."""
    search = _parse()
    assert (search.year_start, search.year_end) == (2023, 2023)
    assert (search.mileage_start, search.mileage_end) == (0, 60_000)


def test_one_open_ended_band_leaves_the_whole_search_open_ended():
    search = _parse(band=[{"year": 2023, "max_bid_jpy": {"private": 1}}])
    assert search.mileage_end is None


def test_a_band_prices_a_car_by_year_and_mileage():
    search = _parse()
    assert search.band_for(2023, 50_000).bid() == 1_805_000
    assert search.band_for(2023, 50_001).bid() == 1_705_000
    assert search.band_for(2022, 10_000) is None      # the year is exact


def test_a_missing_rental_price_falls_back_to_private():
    """The dearer of the two, so not the cautious choice — the only one that
    always resolves, since some cars have no rental price at all."""
    band = _parse().bands[0]
    assert band.bid("rental") == band.bid("private") == 1_805_000


def test_a_band_without_a_private_price_is_refused():
    with pytest.raises(SearchDefinitionError, match="must set 'private'"):
        _parse(band=[{"year": 2023, "max_bid_jpy": {"rental": 1}}])


# --- the errors that would otherwise render a plausible page -----------------


def test_two_bands_that_could_both_price_one_car_are_caught_at_load():
    """Checked as overlap rather than "this lot matched twice": at load there is
    no lot, and a shadowed band is a wrong price you never see."""
    with pytest.raises(SearchDefinitionError, match="overlap"):
        _parse(band=[
            {"year": 2023, "mileage_end": 50_000, "max_bid_jpy": {"private": 1}},
            {"year": 2023, "mileage_start": 40_000, "max_bid_jpy": {"private": 2}},
        ])


def test_bands_in_different_years_never_overlap():
    search = _parse(band=[
        {"year": 2023, "mileage_end": 50_000, "max_bid_jpy": {"private": 1}},
        {"year": 2024, "mileage_end": 50_000, "max_bid_jpy": {"private": 2}},
    ])
    assert len(search.bands) == 2


@pytest.mark.parametrize("bounds, complaint", [
    ({"year_start": 2024}, "own year 2023"),
    ({"mileage_end": 40_000}, "own mileage ceiling"),
    ({"mileage_start": 10_000}, "own mileage floor"),
])
def test_competitor_bounds_narrower_than_their_own_band_are_refused(bounds, complaint):
    """The car you are importing must count as competition for itself. Bounds
    that exclude it are always a typo, and invisible: the list is just shorter,
    and shorter reads as good news."""
    with pytest.raises(SearchDefinitionError, match=complaint):
        _parse(band=[{"year": 2023, "mileage_start": 0, "mileage_end": 50_000,
                      "max_bid_jpy": {"private": 1}, "competitors": bounds}])


def test_an_open_ended_band_needs_open_ended_competitors():
    with pytest.raises(SearchDefinitionError, match="open-ended"):
        _parse(band=[{"year": 2023, "max_bid_jpy": {"private": 1},
                      "competitors": {"mileage_end": 90_000}}])


@pytest.mark.parametrize("key", ["year_start", "year_end", "mileage_start", "mileage_end"])
def test_a_derived_bound_written_in_site_is_an_error_not_an_override(key):
    """An override would be a bound silently disagreeing with the prices under
    it, which is the drift the merge exists to end."""
    with pytest.raises(SearchDefinitionError, match="derived from the bands"):
        _parse(site={key: 2023})


def test_the_car_names_the_make_and_model_so_site_may_not():
    with pytest.raises(SearchDefinitionError, match="named by `car`"):
        _parse(site={"make": "MAZDA"})


def test_a_search_with_no_bands_is_refused():
    """It would have no year or mileage bounds and nothing to bid."""
    with pytest.raises(SearchDefinitionError, match="at least one"):
        _parse(band=[])


def test_an_unknown_car_lists_the_ones_that_exist():
    with pytest.raises(SearchDefinitionError) as exc:
        _parse(car="mazda-cx99")
    assert "mazda-cx30" in str(exc.value)


@pytest.mark.parametrize("payload, complaint", [
    ({"band": [{"year": 2023, "millage_end": 1, "max_bid_jpy": {"private": 1}}]},
     "millage_end"),
    ({"competitors": {"fule_type": []}}, "fule_type"),
    ({"dashboard": {"enabled": "yes"}}, "true or false"),
    ({"competitors": {"fuel_type": "petrol"}}, "must be a list"),
    ({"band": [{"year": 2023, "mileage_start": 60_000, "mileage_end": 50_000,
                "max_bid_jpy": {"private": 1}}]}, "below mileage_start"),
])
def test_a_bad_key_or_value_names_itself(payload, complaint):
    with pytest.raises(SearchDefinitionError, match=complaint):
        _parse(**payload)


def test_an_unknown_section_is_an_error():
    with pytest.raises(SearchDefinitionError, match="dashbord"):
        _parse(dashbord={})


# --- competition and money ---------------------------------------------------


def test_the_crawl_scope_is_the_union_of_the_bands_competitor_bounds():
    """One scrape per search, not one per band: delisting is bounded to a run's
    own scope, so overlapping runs would mean overlapping delisting windows."""
    scope = _parse().competitor_scope()
    assert (scope.year_start, scope.mileage_end) == (2019, 120_000)
    assert scope.year_end is None and scope.mileage_start is None


def test_one_unbounded_band_leaves_that_axis_unbounded_for_the_crawl():
    """Narrowing to the other bands would miss that band's own competitors."""
    scope = _parse(band=[
        {"year": 2023, "mileage_end": 50_000, "max_bid_jpy": {"private": 1},
         "competitors": {"year_start": 2019, "mileage_end": 120_000}},
        {"year": 2024, "mileage_end": 50_000, "max_bid_jpy": {"private": 2},
         "competitors": {"mileage_end": 90_000}},
    ]).competitor_scope()
    assert scope.year_start is None
    assert scope.mileage_end == 120_000


def test_undeclared_bounds_are_not_the_same_as_no_limits():
    """The difference the dashboard needs: nothing declared means nothing to
    crawl and nothing to show, not "every advert qualifies"."""
    assert _parse(band=[{"year": 2023, "max_bid_jpy": {"private": 1}}]) \
        .competitor_scope().declared is False


def test_profit_comes_from_the_search_unless_a_band_overrides_it():
    search = _parse(
        dashboard={"expected_profit_eur": 2000},
        band=[{"year": 2023, "mileage_end": 50_000, "max_bid_jpy": {"private": 1}},
              {"year": 2024, "mileage_end": 50_000, "max_bid_jpy": {"private": 2},
               "expected_profit_eur": 3500}],
    )
    assert search.profit_for(search.bands[0]) == 2000
    assert search.profit_for(search.bands[1]) == 3500


def test_an_absent_dashboard_section_means_enabled():
    """The noisy default on purpose: a search that quietly stayed off the page
    because its section was mistyped would look like a car nobody undercuts."""
    assert _parse().dashboard.enabled is True
    assert _parse(dashboard={"enabled": False}).dashboard.enabled is False


def test_competitor_filters_are_folded_for_matching():
    """So the file never has to know that bazaraki writes ``Hybrid Petrol``."""
    filters = _parse(competitors={"fuel_type": ["Petrol", "Hybrid Petrol"],
                                  "gearbox": "Automatic"}).competitors
    assert filters.fuel_type == ("petrol", "hybrid petrol")
    assert filters.gearbox == "automatic"


# --- the shipped files -------------------------------------------------------


def test_every_shipped_search_loads():
    """A fresh clone must get a working morning; these files are the whole
    configuration of one."""
    for name, search, problem in definition.load_all():
        assert problem is None, f"{name}: {problem}"
        assert search.bands, name


def test_the_provenance_round_trips():
    original = _parse()
    restored = definition.from_provenance({"search": original.to_payload()})
    assert restored.car == original.car
    assert restored.bands == original.bands
