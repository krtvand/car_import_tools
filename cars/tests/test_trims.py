"""Reading a グレード box.

The cases that matter are the ones a real sheet in this repo produced. Every
literal below was read off a scan under ``stats/toyota-harrier-*/sheets/`` or
``runs/2026-08-31_233848_TOYOTA-HARRIER/sheets/``, lot number in the comment
beside it, so a change to the fold is checked against what auction houses
actually type rather than against what this file guessed they might.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cars import trims


def table(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "trims.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- the shipped table -------------------------------------------------------


def test_the_shipped_table_loads_and_ranks_the_harrier_cheapest_first():
    lineup = trims.for_car("toyota-harrier")
    assert [trim.key for trim in lineup] == ["s", "g", "g-leather", "z", "z-leather"]
    assert [trim.rank for trim in lineup] == sorted(trim.rank for trim in lineup)


def test_every_shipped_trim_names_a_car_that_exists():
    """A table filed under a key no search can name would never be reached."""
    from cars import definitions

    for car_key in trims._tables():
        assert car_key in definitions.CARS, car_key


@pytest.mark.parametrize("printed,key,modifiers", [
    # HAA Kobe-style digital sheets, one line per real lot.
    ("G", "g", ()),                                  # lots 2019, 12349, 23961
    ("S", "s", ()),                                  # lots 17119, 72031
    ("S 4WD", "s", ("4WD",)),                        # lot 12158
    ("Z レザーパッケージ", "z-leather", ()),           # lot 30495
    # Spellings the fold has to reach rather than the table listing them.
    ("Ｚ　レザーパッケージ", "z-leather", ()),          # full-width, ideographic space
    ("Z ﾚｻﾞｰﾊﾟｯｹｰｼﾞ", "z-leather", ()),                # half-width katakana
    ('Z "レザーパッケージ"', "z-leather", ()),          # quoted, as the catalogue prints it
    ("Z LEATHER PACKAGE", "z-leather", ()),          # a house that types Roman
    ("ハイブリッドZ", "z", ("hybrid",)),
    ("ハイブリット G 4WD", "g", ("hybrid", "4WD")),    # ハイブリット, as USS lot 23961 spells it
    ("z", "z", ()),                                  # case is folded away
])
def test_a_printed_box_reads_as_the_trim_it_names(printed, key, modifiers):
    reading = trims.read("toyota-harrier", printed)
    assert reading.trim.key == key
    assert reading.modifiers == modifiers
    assert reading.printed == printed, "the sheet's own text is never rewritten"


def test_the_english_name_carries_the_modifiers_the_box_printed():
    assert trims.read("toyota-harrier", "S 4WD").en == "S 4WD"
    assert trims.read("toyota-harrier", "Z レザーパッケージ").en == 'Z "Leather Package"'


# --- what it refuses to guess ------------------------------------------------


def test_a_leather_z_is_never_read_as_a_plain_z():
    """The €3,000 mistake, and the reason the match is an equality.

    A containment match ordered shortest-first would answer ``z`` here, and the
    card would say the cheaper car with no marker that anything was assumed.
    """
    assert trims.read("toyota-harrier", "Z レザーパッケージ").trim.key == "z-leather"


def test_a_grade_from_a_generation_nobody_priced_is_unmatched_not_guessed():
    """``PROGRESS`` contains both G and S. A 60系 Harrier is not a trim of an 80系."""
    reading = trims.read("toyota-harrier", "PROGRESS")
    assert reading.trim is None
    assert reading.printed == "PROGRESS"
    assert reading.en is None


def test_an_unlisted_word_beside_the_trim_costs_the_gloss_not_the_text():
    reading = trims.read("toyota-harrier", "Z レザーパッケージ 寒冷地")
    assert not reading.matched
    assert reading.describe() == "Z レザーパッケージ 寒冷地"


def test_the_trims_own_words_may_not_be_reordered():
    """The modifiers move freely; the trim's own wording does not.

    ``4WD`` is lifted out wherever in the box it sits, because the listings show
    it moving around the grade word. The grade word itself is matched whole,
    so a house inventing ``レザーパッケージ Z`` gets no gloss rather than a
    guess — the same trade the equality makes everywhere else.
    """
    assert trims.read("toyota-harrier", "4WD S").trim.key == "s"
    assert trims.read("toyota-harrier", "レザーパッケージ Z").trim is None


def test_a_box_holding_only_a_modifier_matches_nothing():
    """Lifting the modifier would leave an empty string, which must not match."""
    assert trims.read("toyota-harrier", "ハイブリッド").trim is None


def test_a_car_with_no_table_reads_every_box_as_unmatched():
    """The CX-5 — a car this repo searches for and has never read a sheet for."""
    reading = trims.read("mazda-cx5", "25S プロアクティブ")
    assert reading.trim is None
    assert reading.printed == "25S プロアクティブ"


def test_a_blank_box_is_none_rather_than_an_unmatched_reading():
    """No answer and a wrong answer are different; so are no answer and a blank."""
    assert trims.read("toyota-harrier", None) is None
    assert trims.read("toyota-harrier", "   ") is None


def test_a_phev_is_not_read_as_a_hybrid():
    """Longest-first on the modifiers: プラグインハイブリッド contains ハイブリッド."""
    assert trims.read("toyota-harrier", "プラグインハイブリッド Z").modifiers == ("PHEV",)


# --- the fold ----------------------------------------------------------------


def test_the_fold_keeps_the_long_vowel_mark_inside_レザー():
    """``ー`` is a modifier letter, not punctuation. Dropping it unmatches every
    leather Z."""
    assert trims.fold("レザーパッケージ") == "レザーパッケージ"


def test_the_fold_drops_the_separators_a_house_might_type():
    assert trims.fold("Z・レザー パッケージ") == trims.fold("Zレザーパッケージ")


# --- refusals at load --------------------------------------------------------


def test_a_trim_with_no_english_name_is_refused(tmp_path):
    path = table(tmp_path, '[toyota-harrier]\n[[toyota-harrier.trim]]\n'
                           'key = "z"\nja = ["Z"]\nrank = 1\n')
    with pytest.raises(trims.TrimTableError, match="no en"):
        trims.for_car("toyota-harrier", path)


def test_a_repeated_trim_key_is_refused(tmp_path):
    path = table(tmp_path,
                 '[toyota-harrier]\n'
                 '[[toyota-harrier.trim]]\nkey = "z"\nen = "Z"\nja = ["Z"]\nrank = 1\n'
                 '[[toyota-harrier.trim]]\nkey = "z"\nen = "Z2"\nja = ["ZZ"]\nrank = 2\n')
    with pytest.raises(trims.TrimTableError, match="repeats a trim key"):
        trims.for_car("toyota-harrier", path)
