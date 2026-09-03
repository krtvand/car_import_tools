"""The term glossary.

Two things carry the whole value of this module, and they are what is tested
here:

1. **A term is paid for once.** The folding decides that: if ``ｴｱB`` and
   ``エアB`` key differently, or if ``★純正ナビ`` keys differently from
   ``純正ナビ``, then every house's spelling is a separate purchase and the file
   grows without the glosses ever catching up.
2. **A gloss is never filed against the wrong term.** The answers come back from
   a model in a list, and matching them by position would put "spare key" beside
   欠品 the first time one entry was dropped — silently, and permanently, since
   the file is written once and read for ever.

The API itself is stubbed throughout. What a real call returns is the sheet
prompt's problem (``test_sheets.py``); what is under test here is everything
around it.
"""
from __future__ import annotations

import json

import pytest

from banzai24 import glossary


# --- folding -----------------------------------------------------------------


@pytest.mark.parametrize("printed,expected", [
    ("ｴｱB", "エアB"),                    # half-width katakana, as USS types it
    ("ﾋﾟSD欠品", "ピSD欠品"),            # …including the combining voiced mark
    ("ＳＤ欠品", "SD欠品"),               # full-width Latin, as another house types it
    ("★純正ナビ", "純正ナビ"),            # a selling point's decoration
    ("AA初出品！", "AA初出品"),           # …and its exclamation
    ("★オークションデビュー★", "オークションデビュー"),
    ("・ワイヤレス充電(Qi)", "ワイヤレス充電(Qi)"),   # a warnings-box bullet
    ("  PS  ", "PS"),
])
def test_spellings_of_one_term_fold_to_one_key(printed, expected):
    assert glossary.key(printed) == expected


def test_punctuation_alone_is_not_a_term():
    """Splitting a free-text box on whitespace produces the odd stray bracket.
    A file with an entry for "(" is a file nobody trusts."""
    assert glossary.key("（") == ""
    assert glossary.key("・") == ""
    assert glossary.key("") == ""
    assert glossary.key(None) == ""


def test_case_is_not_folded():
    """``PS`` is power steering. ``ps`` is nothing, and glossing it as power
    steering would be an invention."""
    assert glossary.key("PS") != glossary.key("ps")


# --- splitting the warnings box ----------------------------------------------


def test_the_warnings_box_splits_on_every_width_of_space():
    assert glossary.split_warnings("取保　スペアキー　後送") == [
        "取保", "スペアキー", "後送"]
    assert glossary.split_warnings("デジタルインナーミラーＳＤ欠品\nナビなし") == [
        "デジタルインナーミラーＳＤ欠品", "ナビなし"]
    assert glossary.split_warnings(None) == []
    assert glossary.split_warnings("   ") == []


def test_a_sentence_in_the_box_is_not_split_at_its_comma():
    """`、` is a comma inside a sentence at least as often as it is a separator
    between items. Splitting there would file half a sentence in the glossary
    for ever — and the file is written once and read for ever."""
    box = "R5年2月14日走行18380km時、メーター交換 現ODO5390km"
    assert glossary.split_warnings(box) == [
        "R5年2月14日走行18380km時、メーター交換", "現ODO5390km"]


def test_a_stranded_bracket_is_not_an_item():
    """Sheets bracket a group — `（保取 スペアキー 後日）` — and splitting on the
    spaces inside strands the brackets. A card line reading `（` says nothing."""
    assert glossary.split_warnings("（　保取　スペアキー　後日　）") == [
        "保取", "スペアキー", "後日"]
    # A bracket the sheet typed against a word is part of that word, and prints.
    assert glossary.split_warnings("（保取）") == ["（保取）"]


# --- what is missing ---------------------------------------------------------


def test_only_terms_nobody_has_been_asked_about_are_missing():
    table = {"PS": "power steering", "後送": None}
    assert glossary.missing(["PS", "PW", "後送", "PW"], table) == ["PW"]


def test_a_term_recorded_as_unreadable_is_never_asked_about_again():
    """`null` is an answer, not a hole. Treating it as missing would re-send
    every unglossable term on every run, for ever, at full price."""
    assert glossary.missing(["取保"], {"取保": None}) == []


# --- asking, and keeping the answer -------------------------------------------


class _FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, pairs):
        self.parsed_output = glossary.Glossary(
            terms=[glossary.Gloss(ja=ja, en=en) for ja, en in pairs]
        )


class _FakeClient:
    """Answers with whatever it is told to, and records what it was asked."""

    def __init__(self, answers, stop_reason="end_turn"):
        self.answers, self.asked = answers, []
        self.messages = self
        self._stop_reason = stop_reason

    def parse(self, **params):
        asked = params["messages"][0]["content"].split("\n")
        self.asked.append(asked)
        response = _FakeResponse([(term, self.answers.get(term)) for term in asked])
        response.stop_reason = self._stop_reason
        return response


def test_a_gloss_is_matched_to_its_term_by_name_not_by_position():
    """The one failure this module must not have. A model that drops or reorders
    one entry would otherwise shift every gloss after it onto the wrong term —
    no error, no warning, and a file that is wrong for ever."""
    client = _FakeClient({})
    client.parse = lambda **params: _FakeResponse([
        ("後送", "to be sent later"),          # answered out of order,
        ("スペアキー", "spare key"),           # …with the middle term missing
    ])

    glosses = glossary.translate(["取保", "スペアキー", "後送"], client=client)
    assert glosses == {"スペアキー": "spare key", "後送": "to be sent later"}


def test_an_answer_about_something_nobody_asked_is_dropped():
    client = _FakeClient({})
    client.parse = lambda **params: _FakeResponse([("ナビ", "navigation")])
    assert glossary.translate(["PS"], client=client) == {}


def test_a_long_list_is_asked_in_batches(monkeypatch):
    monkeypatch.setattr(glossary, "BATCH", 2)
    client = _FakeClient({"a": "A", "b": "B", "c": "C"})
    glossary.translate(["a", "b", "c"], client=client)
    assert client.asked == [["a", "b"], ["c"]]


def test_a_refusal_is_an_error_not_an_empty_glossary():
    """Recording nothing would be indistinguishable from "the model read these
    and had nothing to say", and `ensure` would write nulls over every one of
    them — permanently, since a null is never asked about again."""
    client = _FakeClient({"PS": "power steering"}, stop_reason="refusal")
    with pytest.raises(RuntimeError):
        glossary.translate(["PS"], client=client)


def test_ensure_writes_what_it_learned_and_asks_only_once(isolated_glossary):
    client = _FakeClient({"PS": "power steering", "PW": "power windows"})

    learned = glossary.ensure(["PS", "PW"], client=client, path=isolated_glossary)
    assert learned == {"PS": "power steering", "PW": "power windows"}
    assert json.loads(isolated_glossary.read_text(encoding="utf-8"))["PW"] == \
        "power windows"

    # The second sheet to print them costs nothing at all — no request is made.
    assert glossary.ensure(["PS", "ｴｱB", "PW"], client=client,
                           path=isolated_glossary) == {"エアB": None}
    assert client.asked == [["PS", "PW"], ["エアB"]]


def test_a_term_the_model_could_not_read_is_recorded_as_unreadable(isolated_glossary):
    """Otherwise it is re-sent on every run for ever. A `null` costs one line in
    the file and answers the question once."""
    client = _FakeClient({})            # answers every term with no gloss
    glossary.ensure(["謎の略号"], client=client, path=isolated_glossary)

    assert glossary.load(isolated_glossary) == {"謎の略号": None}
    glossary.ensure(["謎の略号"], client=client, path=isolated_glossary)
    assert len(client.asked) == 1


def test_a_term_the_model_left_out_is_asked_about_again(isolated_glossary):
    """An answered `null` and a missing line look the same in the returned list
    and are worlds apart. 欠品 is perfectly readable; writing it off as
    unreadable because one batch came back a line short would blank that gloss
    on every card for ever, and nothing would ever ask again."""
    client = _FakeClient({})
    client.parse = lambda **params: _FakeResponse([("PS", "power steering")])

    glossary.ensure(["PS", "欠品"], client=client, path=isolated_glossary)
    assert glossary.load(isolated_glossary) == {"PS": "power steering"}
    assert glossary.missing(["欠品"], glossary.load(isolated_glossary)) == ["欠品"]


def test_a_hand_typed_correction_survives_the_next_run(isolated_glossary):
    """The file is the artefact, not a cache. An operator who fixes a gloss by
    hand must not have it overwritten by the next sheet that prints the term."""
    glossary.save({"純正ナビ": "factory-fitted navigation"}, isolated_glossary)
    client = _FakeClient({"純正ナビ": "pure navigation"})

    glossary.ensure(["純正ナビ"], client=client, path=isolated_glossary)
    assert glossary.load(isolated_glossary)["純正ナビ"] == "factory-fitted navigation"
    assert client.asked == []


def test_terms_learned_by_another_run_are_not_dropped(isolated_glossary):
    """`extract` and `glossary` can be running at once on a two-car morning. The
    file is re-read immediately before writing, so the loser of the race adds
    its terms rather than replacing the winner's."""
    client = _FakeClient({"PS": "power steering"})

    def racing_parse(**params):
        # Another process finishes while this request is in flight.
        glossary.save({"PW": "power windows"}, isolated_glossary)
        return _FakeResponse([("PS", "power steering")])

    client.parse = racing_parse
    glossary.ensure(["PS"], client=client, path=isolated_glossary)

    assert glossary.load(isolated_glossary) == {"PS": "power steering",
                                                "PW": "power windows"}


# --- reading the file ---------------------------------------------------------


def test_a_corrupt_file_costs_glosses_not_the_report(isolated_glossary):
    """The Japanese is on the card either way. A report that failed to render
    because one line of JSON is broken would be the worse outcome by far."""
    isolated_glossary.write_text("{not json", encoding="utf-8")
    assert glossary.load(isolated_glossary) == {}
    assert glossary.gloss(["PS"], glossary.load(isolated_glossary)) == [
        {"ja": "PS", "en": None}]


def test_a_missing_file_is_an_empty_glossary(tmp_path):
    assert glossary.load(tmp_path / "nothing.json") == {}


def test_the_file_is_written_sorted_for_a_readable_diff(isolated_glossary):
    glossary.save({"PW": "power windows", "PS": "power steering"}, isolated_glossary)
    lines = isolated_glossary.read_text(encoding="utf-8").splitlines()
    assert [line.strip().split('"')[1] for line in lines[1:3]] == ["PS", "PW"]


# --- what one sheet contributes ----------------------------------------------


def test_a_sheets_terms_are_its_equipment_and_its_warnings():
    assert glossary.terms_of(["PS", "PW"], "取保　後送") == [
        "PS", "PW", "取保", "後送"]


def test_equipment_is_taken_as_a_list_or_as_the_json_the_database_stores():
    """`extract` has the list; the backfill command has the column. Neither
    should have to know which the other passes."""
    assert glossary.terms_of('["PS", "PW"]', None) == ["PS", "PW"]
    assert glossary.terms_of("not json", "取保") == ["取保"]
    assert glossary.terms_of(None, None) == []
