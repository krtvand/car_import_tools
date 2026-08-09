"""CLI filter resolution — mostly guarding the saved searches from stray inheritance."""
from __future__ import annotations

import argparse

from banzai24 import cli, config


def _args(**kwargs) -> argparse.Namespace:
    base = dict(
        make=None, model=None, transmission=None,
        year_start=None, year_end=None,
        mileage_start=None, mileage_end=None,
        engine_capacity_start=None, engine_capacity_end=None,
        source=None, grade=None, no_defaults=False,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_without_no_defaults_overrides_layer_on_default_filters():
    filters = cli._filters_from_args(_args(make="TOYOTA", model="RAV4"))
    assert filters.make == "TOYOTA"
    # inherited from DEFAULT_FILTERS
    assert filters.year_start == config.DEFAULT_FILTERS.year_start
    assert filters.grade_origin == config.DEFAULT_FILTERS.grade_origin


def test_no_defaults_does_not_inherit_unset_filters():
    """The RAV4 search must not pick up the CX-30's engine-capacity floor."""
    filters = cli._filters_from_args(
        _args(make="TOYOTA", model="RAV4", no_defaults=True, year_start=2023)
    )
    assert filters.make == "TOYOTA"
    assert filters.year_start == 2023
    assert filters.engine_capacity_start is None
    assert filters.mileage_end is None
    assert filters.grade_origin == ()

    # and the omission reaches the URL, not just the dataclass
    assert "engineCapacityStart" not in config.build_search_url(filters)


def test_no_defaults_still_keeps_required_scope_fields():
    filters = cli._filters_from_args(_args(make="TOYOTA", no_defaults=True))
    assert filters.source == "auctions"
    assert filters.country_iso == "JP"


def test_grade_flag_is_repeatable():
    filters = cli._filters_from_args(_args(no_defaults=True, grade=["4", "4.5"]))
    assert filters.grade_origin == ("4", "4.5")


def test_every_overridable_name_exists_on_the_dataclass():
    """A typo'd name here would silently never apply."""
    fields = {f.name for f in config.dataclasses.fields(config.AuctionFilters)} \
        if hasattr(config, "dataclasses") else set()
    if not fields:  # config does not re-export dataclasses; import directly
        import dataclasses as _dc
        fields = {f.name for f in _dc.fields(config.AuctionFilters)}
    assert set(cli._OVERRIDABLE) <= fields


def test_neutral_base_has_no_model_or_transmission():
    assert cli.NEUTRAL_FILTERS.model is None
    assert cli.NEUTRAL_FILTERS.transmission is None


def test_the_flags_the_daily_script_recommends_actually_exist():
    """``searches/daily.sh`` ends by printing the commands for the rest of the
    morning. A renamed or removed flag would make that advice wrong in the one
    place nothing else looks — a shell script's here-doc.

    argparse exits non-zero on an unknown flag, so parsing is the assertion.
    """
    parser = cli._build_parser()

    parser.parse_args(["check"])
    parser.parse_args(["extract", "--today", "--dry-run"])
    parser.parse_args(["extract", "--today", "--limit", "5"])
    parser.parse_args(["extract", "runs/2026-08-09_222421_TOYOTA-RAV4"])
    parser.parse_args(["report", "--today", "--open"])


def test_extract_and_report_both_understand_today():
    """The two commands scope by day the same way, so the daily flow reads the
    sheets it fetched this morning and re-renders the reports for those runs."""
    parser = cli._build_parser()
    assert parser.parse_args(["extract", "--today"]).today is True
    assert parser.parse_args(["report", "--today"]).today is True
    assert parser.parse_args(["extract"]).today is False
    assert parser.parse_args(["report"]).today is False
