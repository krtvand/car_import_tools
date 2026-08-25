"""CLI filter resolution: a saved search, or ad-hoc flags, and never a mixture.

The thing worth guarding is that the two do not blend. A ``--search`` carrying
overrides would be a crawl whose scope no dashboard panel can reproduce — and
``db._in_scope`` bounds delisting to that scope, so the overrides would silently
decide which adverts are allowed to go missing.
"""
from __future__ import annotations

import argparse
import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

import searches

from bazaraki import cli, config


def _args(**kwargs) -> argparse.Namespace:
    base = dict(
        make=None, model=None,
        price_min=None, price_max=None,
        year_min=None, year_max=None,
        mileage_min=None, mileage_max=None,
        search=None,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_a_search_becomes_its_cars_slugs_and_its_competitor_bounds():
    """The whole configuration, from the file — the crawl scope is the union of
    the bands' competitor bounds, which is exactly what the panel asks about."""
    filters = cli._filters_from_args(_args(search="mazda-cx30"))
    scope = searches.load("mazda-cx30").competitor_scope()

    assert (filters.make, filters.model) == ("mazda", "cx-30")
    assert filters.year_min == scope.year_start
    assert filters.mileage_max == scope.mileage_end


def test_a_search_cannot_be_combined_with_the_filter_flags():
    with pytest.raises(SystemExit) as exc:
        cli._filters_from_args(_args(search="mazda-cx30", year_min=2020))
    assert "cannot be combined" in str(exc.value)


def test_nothing_at_all_names_the_searches_rather_than_crawling_everything():
    """There is no DEFAULT_FILTERS to fall back on, and a scopeless crawl would
    delist against the whole cars category."""
    with pytest.raises(SystemExit) as exc:
        cli._filters_from_args(_args())
    assert "mazda-cx30" in str(exc.value)


def test_a_search_with_no_competitor_bounds_is_refused_rather_than_crawled_wide(tmp_path):
    """Wide is slow; the real objection is that the scope would match no panel."""
    (tmp_path / "bare.toml").write_text(
        'car = "mazda-cx5"\n[[band]]\nyear = 2023\nmax_bid_jpy = { private = 1 }\n',
        encoding="utf-8")
    with pytest.raises(config.NoCompetitorBounds):
        config.filters_for(searches.load("bare", tmp_path))


def test_ad_hoc_flags_do_not_inherit_unset_filters():
    """A one-off probe applies what you typed and nothing else."""
    filters = cli._filters_from_args(
        _args(make="toyota", model="toyota-rav4", year_min=2022)
    )
    assert filters.make == "toyota"
    assert filters.year_min == 2022
    assert filters.mileage_max is None
    assert filters.price_max is None

    # and the omission reaches the URL, not just the dataclass
    url = config.build_search_url(filters, year_codes={2022: "77"})
    assert "mileage_max" not in url


def test_mileage_flags_reach_the_query():
    filters = cli._filters_from_args(
        _args(make="mazda", model="cx-30", mileage_min=0, mileage_max=60000)
    )
    query = dict(config.build_query(filters))
    assert query["attrs__mileage_min"] == "0"
    assert query["attrs__mileage_max"] == "60000"


def test_every_overridable_name_exists_on_the_dataclass():
    """A typo'd name here would silently never apply."""
    fields = {f.name for f in dataclasses.fields(config.CarFilters)}
    assert set(cli._OVERRIDABLE) <= fields


def test_neutral_base_has_nothing_set():
    assert cli._describe(cli.NEUTRAL_FILTERS) == "none (whole cars category)"


def test_plan_url_flags_filters_it_cannot_spell_out():
    with_year = config.CarFilters(make="mazda", model="cx-30", year_min=2022)
    assert cli._plan_url(with_year).endswith("resolved from the live page)")

    without_year = config.CarFilters(make="mazda", model="cx-30", price_max=25000)
    assert cli._plan_url(without_year).endswith("/mazda/cx-30/?price_max=25000")


@pytest.mark.parametrize("name", searches.available())
def test_every_saved_search_resolves_to_a_crawl_or_says_why_not(name: str):
    """A search either names a scope bazaraki can crawl, or refuses with the
    reason. What it may never do is quietly produce a filter set that matches
    the wrong car — the slugs are not derivable, so an absent one must raise."""
    search = searches.load(name)
    try:
        filters = config.filters_for(search)
    except config.NoCompetitorBounds as exc:
        assert name in str(exc)
        return
    assert filters.make and filters.model
    assert config.base_path(filters).endswith(f"/{filters.make}/{filters.model}/")
