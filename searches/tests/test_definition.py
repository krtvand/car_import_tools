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


def test_two_variants_of_one_car_are_two_bands_at_two_prices():
    """The RAV4 is why the code sits on the band. The E-Four AXAH54 and the 2WD
    AXAH52 are one year, one mileage range and ¥445,000 apart, so a search-wide
    code could only ever price one of them."""
    search = _parse(car="toyota-rav4", band=[
        {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 3_150_000}},
        {"year": 2023, "body_model_code": ["AXAH52"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 2_705_000}},
    ])
    assert search.band_for(2023, 20_000, "AXAH54").bid() == 3_150_000
    assert search.band_for(2023, 20_000, "AXAH52").bid() == 2_705_000


def test_a_car_whose_code_nobody_stated_is_priced_by_no_band():
    """Not the dearer band and not the first one: which variant it is decides
    ¥445,000, and nothing has said which variant it is."""
    search = _parse(car="toyota-rav4", band=[
        {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 3_150_000}}])
    assert search.band_for(2023, 20_000) is None
    assert search.band_for(2023, 20_000, "AXAH52") is None
    # The type prefix banzai24 carries on some lots and not others is no
    # obstacle: the code is matched as a substring, the way `[api]` always did.
    assert search.band_for(2023, 20_000, "6AA-AXAH54").bid() == 3_150_000


def test_a_band_naming_no_code_prices_every_code():
    """Which is what every single-variant search says by saying nothing."""
    band = _parse().bands[0]
    assert band.prices_code("DMEJ3P") and band.prices_code(None)


def test_the_codes_the_fetch_keeps_are_the_union_of_the_bands():
    """Derived for the reason the year and mileage bounds are: written twice,
    they drift into a lot that arrives and then cannot be priced."""
    search = _parse(car="toyota-rav4", band=[
        {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 3_150_000}},
        {"year": 2023, "body_model_code": ["AXAH52"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 2_705_000}},
    ])
    assert search.body_model_code == ("AXAH54", "AXAH52")


def test_one_band_without_a_code_leaves_the_fetch_unnarrowed():
    """That band prices every variant, so narrowing the fetch to the codes its
    neighbours name would drop the very lots it exists to price."""
    search = _parse(band=[
        {"year": 2023, "body_model_code": ["DMEJ3P"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 1}},
        {"year": 2024, "mileage_end": 50_000, "max_bid_jpy": {"private": 2}},
    ])
    assert search.body_model_code == ()


def test_a_missing_rental_price_falls_back_to_private():
    """The dearer of the two, so not the cautious choice — the only one that
    always resolves, since some cars have no rental price at all."""
    band = _parse().bands[0]
    assert band.bid("rental") == band.bid("private") == 1_805_000


def test_a_band_without_a_private_price_is_refused():
    with pytest.raises(SearchDefinitionError, match="must set 'private'"):
        _parse(band=[{"year": 2023, "max_bid_jpy": {"rental": 1}}])


def test_a_band_may_narrow_its_own_statistics():
    """Handed on unread — the keys belong to banzai24 — but kept per band,
    because the trim a chassis code implies differs band to band."""
    search = _parse(band=[{"year": 2023, "max_bid_jpy": {"private": 1},
                           "auction_statistics": {"model_grade": ["X"]}}])
    assert search.bands[0].auction_statistics == {"model_grade": ["X"]}


def test_a_band_that_narrows_nothing_carries_an_empty_table():
    assert _parse().bands[0].auction_statistics == {}


# --- the errors that would otherwise render a plausible page -----------------


def test_two_bands_that_could_both_price_one_car_are_caught_at_load():
    """Checked as overlap rather than "this lot matched twice": at load there is
    no lot, and a shadowed band is a wrong price you never see."""
    with pytest.raises(SearchDefinitionError, match="overlap"):
        _parse(band=[
            {"year": 2023, "mileage_end": 50_000, "max_bid_jpy": {"private": 1}},
            {"year": 2023, "mileage_start": 40_000, "max_bid_jpy": {"private": 2}},
        ])


def test_bands_that_price_different_codes_never_overlap():
    """Same year, same kilometres, different variant: that is the point of a
    code on a band, not a clash."""
    search = _parse(car="toyota-rav4", band=[
        {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 3_150_000}},
        {"year": 2023, "body_model_code": ["AXAH52"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 2_705_000}},
    ])
    assert len(search.bands) == 2


def test_a_shorter_code_that_swallows_its_neighbour_still_overlaps():
    """``AXAH5`` and ``AXAH54`` are not two variants — they are one band
    shadowing another, and a shadowed band is a price you never see."""
    with pytest.raises(SearchDefinitionError, match="overlap"):
        _parse(car="toyota-rav4", band=[
            {"year": 2023, "body_model_code": ["AXAH5"], "mileage_end": 50_000,
             "max_bid_jpy": {"private": 1}},
            {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
             "max_bid_jpy": {"private": 2}},
        ])


def test_a_coded_band_and_an_uncoded_one_still_overlap():
    """The uncoded band prices every code, this one included."""
    with pytest.raises(SearchDefinitionError, match="overlap"):
        _parse(car="toyota-rav4", band=[
            {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
             "max_bid_jpy": {"private": 1}},
            {"year": 2023, "mileage_end": 50_000, "max_bid_jpy": {"private": 2}},
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


def test_the_code_written_in_api_says_where_it_went():
    """It used to live there, and one search-wide code is a second answer to the
    question a band now answers with a price attached."""
    with pytest.raises(SearchDefinitionError, match=r"\[\[band\]\] key now"):
        _parse(api={"body_model_code": ["DMEJ3P"]})


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


def test_a_band_excludes_a_phrase_the_search_as_a_whole_does_not():
    """The RAV4's two bands are opposites about the same word: the AXAH54 is the
    G and an advert saying "hybrid x" undercuts it, while the AXAH52 *is* the X
    and those adverts are the market it sells into."""
    search = _parse(car="toyota-rav4", competitors={"fuel_type": ["hybrid petrol"]},
                    band=[
        {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 3_150_000},
         "competitors": {"year_start": 2022, "mileage_end": 70_000,
                         "exclude_phrases": ["Hybrid X"]}},
        {"year": 2023, "body_model_code": ["AXAH52"], "mileage_end": 50_000,
         "max_bid_jpy": {"private": 2_705_000},
         "competitors": {"year_start": 2022, "mileage_end": 70_000}},
    ])
    g, x = search.bands
    assert search.competitors_for(g).exclude_phrases == ("hybrid x",)
    assert search.competitors_for(x).exclude_phrases == ()
    # Folded the same way the search-wide ones are, and the rest of the block
    # comes along: a band narrows the filters, it does not replace them.
    assert search.competitors_for(g).fuel_type == ("hybrid petrol",)


def test_a_band_adds_to_the_searchs_phrases_rather_than_replacing_them():
    """A phrase the whole search does not sell against is not a phrase one band
    does, so there is no way to un-exclude from a band."""
    search = _parse(competitors={"exclude_phrases": ["x package"]},
                    band=[{"year": 2023, "mileage_end": 50_000,
                           "max_bid_jpy": {"private": 1},
                           "competitors": {"year_start": 2019, "mileage_end": 120_000,
                                           "exclude_phrases": ["hybrid x"]}}])
    assert search.competitors_for(search.bands[0]).exclude_phrases == (
        "x package", "hybrid x")


def test_a_band_that_only_excludes_a_phrase_has_still_declared_no_bounds():
    """It has said nothing about which adverts compete with it, and the
    dashboard has to say so rather than crawl every year of the car."""
    search = _parse(band=[{"year": 2023, "mileage_end": 50_000,
                           "max_bid_jpy": {"private": 1},
                           "competitors": {"exclude_phrases": ["hybrid x"]}}])
    assert search.bands[0].competitors.declared is False


def test_a_bands_phrases_do_not_narrow_the_crawl():
    """The crawl is one scrape shared by every band, and the phrases are applied
    to it in memory afterwards. Excluding at the crawl would delete an advert
    from every band's evidence because one band did not want it."""
    scope = _parse(band=[{"year": 2023, "mileage_end": 50_000,
                          "max_bid_jpy": {"private": 1},
                          "competitors": {"year_start": 2019, "mileage_end": 120_000,
                                          "exclude_phrases": ["hybrid x"]}}]).competitor_scope()
    assert scope.exclude_phrases == ()


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


def test_excluded_phrases_are_folded_the_same_way():
    """Written as the seller would type them, matched however they typed them."""
    filters = _parse(competitors={"exclude_phrases": ["Hybrid X", "X package"]}).competitors
    assert filters.exclude_phrases == ("hybrid x", "x package")
    assert filters.declared


# --- the shipped files -------------------------------------------------------


def test_every_shipped_search_loads():
    """A fresh clone must get a working morning; these files are the whole
    configuration of one."""
    for name, search, problem in definition.load_all():
        assert problem is None, f"{name}: {problem}"
        assert search.bands, name


def test_a_run_recorded_before_the_code_moved_still_reads_back():
    """One code for the whole search is the same code on each of its bands, so
    the old shape converts exactly. Dropping it would re-render an old morning
    with the filter switched off — a wider report that still looks measured."""
    stored = _parse().to_payload()
    stored["api"] = {"body_model_code": ["DMEJ3P"]}
    for band in stored["bands"]:
        band.pop("body_model_code")
    restored = definition.from_provenance({"search": stored})
    assert all(band.body_model_code == ("DMEJ3P",) for band in restored.bands)


def test_reading_a_run_back_does_not_edit_the_run():
    """The payload is the run's own file as its caller still holds it."""
    payload = {"search": {**_parse().to_payload(),
                          "api": {"body_model_code": ["DMEJ3P"]}}}
    definition.from_provenance(payload)
    assert payload["search"]["api"] == {"body_model_code": ["DMEJ3P"]}


def test_the_provenance_round_trips():
    original = _parse()
    restored = definition.from_provenance({"search": original.to_payload()})
    assert restored.car == original.car
    assert restored.bands == original.bands


def test_a_bands_own_statistics_survive_the_provenance_copy():
    """Recorded like everything else on a band. A run read back from a renamed
    file would otherwise measure the E-Four against every trim line at once."""
    original = _parse(band=[{"year": 2023, "max_bid_jpy": {"private": 1},
                             "auction_statistics": {"model_grade": ["X"]}}])
    restored = definition.from_provenance({"search": original.to_payload()})
    assert restored.bands[0].auction_statistics == {"model_grade": ["X"]}
