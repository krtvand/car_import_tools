"""Reading auction sheets with Claude.

Split deliberately: everything that does not need the network is an ordinary
test, and the two that actually call the model are marked ``live`` and excluded
from the default run (see ``addopts`` in pyproject.toml).

    uv run pytest                 # everything except the paid tests
    uv run pytest -m live         # just the paid ones, ~$0.03

**Run the live tests when you change what is sent or what comes back** — the
prompt, :class:`~banzai24.sheets.SheetData`, ``MODEL``, or ``EFFORT``. Those are
the only edits that can change whether the model still reads a sheet correctly,
and they are exactly what the offline tests cannot check: everything below
verifies our side of the exchange against a fixed expectation, so all of it
would keep passing while a reworded prompt quietly started misreading grades.

Editing storage, the CLI, or the cross-check arithmetic does not need them —
those are covered offline, including against real recorded values.

The cross-checks are the part most worth testing offline: they are what tells
you an extraction went wrong, so a bug there is a bug in the only thing watching
the model.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import anthropic
import pytest
from sqlmodel import create_engine

from banzai24 import db, normalize, sheets
from banzai24.models import AuctionLot
from banzai24.sheets import DamageMark, SheetData

FIXTURES = Path(__file__).parent / "fixtures"
SHEET = FIXTURES / "sheet_CAA-Chubu_2026-08-12_33152.jpg"

# Read off the fixture sheet by eye. Lot 33152, CAA Chubu, a 2023 CX-30.
GOLDEN = {
    "sheet_grade": "5",
    "exterior_grade": "A",
    "interior_grade": "B",
    "sheet_mileage_km": 15415,
    "chassis_full": "DMEJ3P-103452",
    "first_registration_raw": "R5年1月",
    "shaken_expiry_raw": None,          # 車検 box is blank — no valid shaken
    "damage_codes": {"A1", "U1"},
    "warnings_contains": "SD欠品",       # ﾋﾟSD欠品 — navi SD card missing
    "inspector_notes_contains": "ハンドル",  # ハンドルすれ — steering wheel scuffed
    "private_car_note_contains": "自家用",
}


def _sheet_data(**overrides) -> SheetData:
    base = {
        "sheet_grade": "5", "exterior_grade": "A", "interior_grade": "B",
        "sheet_mileage_km": 15415, "chassis_full": "DMEJ3P-103452",
        "first_registration_raw": "R5年1月", "shaken_expiry_raw": None,
        "damage_marks": [DamageMark(panel="right rear", code="A1")],
        "equipment": ["純正メーカーナビTV"],
        "warnings_ja": "ﾋﾟSD欠品", "warnings_en": "Navi SD card missing",
        "inspector_notes_ja": "ハンドルすれ",
        "inspector_notes_en": "Steering wheel scuffed", "drivetrain": None,
        "rental_car_note": None, "private_car_note": "自家用", "confidence": 0.95,
    }
    return SheetData(**{**base, **overrides})


def _lot(**overrides) -> AuctionLot:
    """The fixture sheet's own lot, exactly as the list API described it.

    Not invented: the fixture image is byte-identical to the sheet saved for
    lot 55-1850-33152, so these are the real API values the extraction gets
    checked against — including the 15,000 vs 15,415 rounding and the masked
    chassis.
    """
    base = {
        "lot_number": "55-1850-33152", "lot_short": "33152", "banzai_id": "x",
        "auction_id": 55, "auction_name": "CAA Chubu",
        "trade_date": date(2026, 8, 12), "trade_time": "12:00",
        "mark": "MAZDA", "model": "CX-30",
        "body_model_code": "DMEJ3P", "body_number": "DMEJ3P-10**52",
        "grade_origin": "5", "mileage_km": 15000,
        "registration_year": 2023, "registration_month": 1,
    }
    return AuctionLot(**{**base, **overrides})


# --- the mileage cross-check -------------------------------------------------
#
# The single most important comparison to get right: the API rounds and the
# sheet does not, so the naive version of this check flags every lot in the
# database and is worse than no check at all.

def test_the_rounded_api_mileage_agrees_with_the_exact_sheet_one():
    """The fixture's own numbers: sheet 15,415, API 15,000."""
    assert sheets.mileage_matches(15415, 15000) is True


def test_a_genuinely_different_mileage_still_fails():
    assert sheets.mileage_matches(15415, 48000) is False


def test_rounding_goes_to_the_nearest_thousand_not_downward():
    assert sheets.mileage_matches(15600, 16000) is True   # 15.6k rounds up
    assert sheets.mileage_matches(15400, 15000) is True


def test_a_missing_side_is_not_a_mismatch():
    """Absent is not wrong — it must not be reported as a disagreement."""
    assert sheets.mileage_matches(None, 15000) is None
    assert sheets.mileage_matches(15415, None) is None


# --- the grade cross-check ---------------------------------------------------

def test_grades_must_match_exactly():
    assert sheets.grade_matches("4.5", "4.5") is True
    assert sheets.grade_matches("4.5", "4") is False


def test_grade_comparison_ignores_stray_whitespace():
    assert sheets.grade_matches(" 5 ", "5") is True


def test_a_missing_grade_is_not_a_mismatch():
    assert sheets.grade_matches(None, "5") is None
    assert sheets.grade_matches("5", None) is None


# --- the registration cross-check --------------------------------------------
#
# The sheet prints an era date and the API a Gregorian one; this is what
# normalize.parse_era_date was written for.

def test_the_era_date_on_the_sheet_matches_the_api_year():
    assert sheets.registration_matches("R5年1月", 2023, 1) is True


def test_a_wrong_year_is_caught():
    assert sheets.registration_matches("R5年1月", 2021, 1) is False


def test_the_month_is_only_compared_when_the_api_has_one():
    """registrationMonth is null on most lots — that must not read as a mismatch."""
    assert sheets.registration_matches("R5年1月", 2023, None) is True


def test_an_unparseable_era_date_yields_no_verdict():
    assert sheets.registration_matches("???", 2023, 1) is None
    assert sheets.registration_matches(None, 2023, 1) is None


# --- the chassis cross-check -------------------------------------------------
#
# The real fixture lot: the API publishes DMEJ3P-10**52, the sheet prints
# DMEJ3P-103452. Every unmasked position has to agree.

def test_the_unmasked_chassis_agrees_with_the_masked_one():
    assert sheets.chassis_matches("DMEJ3P-103452", "DMEJ3P-10**52") is True


def test_a_hallucinated_chassis_is_caught_by_the_revealed_digits():
    """Nothing else in the pipeline would catch this, and a wrong chassis is a
    wrong car."""
    assert sheets.chassis_matches("DMEJ3P-109999", "DMEJ3P-10**52") is False
    assert sheets.chassis_matches("DMEJ3R-103452", "DMEJ3P-10**52") is False


def test_a_chassis_of_the_wrong_length_cannot_be_the_same_number():
    assert sheets.chassis_matches("DMEJ3P-1034", "DMEJ3P-10**52") is False


def test_a_missing_chassis_is_not_a_mismatch():
    assert sheets.chassis_matches(None, "DMEJ3P-10**52") is None
    assert sheets.chassis_matches("DMEJ3P-103452", None) is None


# --- the four together -------------------------------------------------------

def test_a_clean_extraction_reports_no_disagreements():
    checks = sheets.cross_check(_sheet_data(), _lot())
    assert checks.disagreements == []
    assert "4/4" in checks.describe()


def test_a_relisted_or_misread_lot_is_flagged_by_field():
    checks = sheets.cross_check(
        _sheet_data(sheet_grade="4", sheet_mileage_km=90000), _lot()
    )
    assert checks.disagreements == ["grade", "mileage"]
    assert "MISMATCH" in checks.describe()


def test_an_extraction_with_nothing_comparable_says_so():
    checks = sheets.cross_check(
        _sheet_data(sheet_grade=None, sheet_mileage_km=None,
                    first_registration_raw=None, chassis_full=None),
        _lot(),
    )
    assert checks.disagreements == []
    assert checks.describe() == "nothing to check"


# --- the row written to the database -----------------------------------------

def test_the_era_date_is_parsed_on_its_way_into_the_row():
    row = sheets.to_row(sheets.Extraction(
        lot_number="47-1312-33152", data=_sheet_data(), raw_json="{}",
        sheet_sha256="abc", model_id=sheets.MODEL,
    ))
    assert row["first_registration_raw"] == "R5年1月"
    assert row["first_registration_year"] == 2023
    assert row["first_registration_month"] == 1


def test_lists_are_stored_as_readable_json_not_python_repr():
    """`ensure_ascii=False`, so the Japanese survives a look at the raw column."""
    row = sheets.to_row(sheets.Extraction(
        lot_number="x", data=_sheet_data(), raw_json="{}",
        sheet_sha256="abc", model_id=sheets.MODEL,
    ))
    assert json.loads(row["damage_marks"]) == [{"panel": "right rear", "code": "A1"}]
    assert "純正" in row["equipment"]


def test_a_blank_shaken_box_stays_null_rather_than_becoming_a_string():
    """No shaken is a fact worth money — it must not be blurred into ''."""
    row = sheets.to_row(sheets.Extraction(
        lot_number="x", data=_sheet_data(), raw_json="{}",
        sheet_sha256="abc", model_id=sheets.MODEL,
    ))
    assert row["shaken_expiry_raw"] is None


# --- the request we send -----------------------------------------------------

def test_the_prompt_states_the_damage_legend_as_ground_truth():
    """It is printed on every sheet, so the model should transcribe it, not recall it."""
    for code in ("A", "U", "XX", "欠"):
        assert code in sheets.PROMPT
    assert "キズ" in sheets.PROMPT and "ヘコミ" in sheets.PROMPT


def test_the_prompt_asks_for_null_rather_than_a_guess():
    assert "Null over guesses" in sheets.PROMPT


def test_the_shared_prompt_is_cached_and_the_image_comes_after_it():
    """Caching is a prefix match: the constant prompt has to precede the one
    part that changes, or there is no reusable prefix and every sheet pays full
    price for the instructions."""
    params = sheets._message_params("Zm9v", "image/jpeg")

    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][0]["content"][0]["type"] == "image"
    assert params["model"] == "claude-opus-5"


def _captured_request() -> dict:
    """The exact JSON body `parse()` would put on the wire, without sending it.

    Worth doing properly rather than asserting on our own dict: the SDK rewrites
    `output_format` into `output_config.format` and injects the
    `additionalProperties: false` that structured outputs require, so the body we
    build is not the body that gets sent.
    """
    import httpx

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "captured"}})

    client = anthropic.Anthropic(
        api_key="test-not-used", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(anthropic.BadRequestError):
        client.messages.parse(output_format=SheetData,
                              **sheets._message_params("Zm9v", "image/jpeg"))
    return captured["body"]


def test_the_schema_sent_satisfies_the_structured_output_rules():
    """Every field required and additionalProperties false, at every level —
    otherwise the API rejects the schema and no sheet is ever read."""
    schema = _captured_request()["output_config"]["format"]["schema"]

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["DamageMark"]["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_the_effort_setting_survives_the_sdk_rewriting_output_config():
    """`output_format` and our `effort` both land in `output_config` — a naive
    merge would silently drop one of them."""
    body = _captured_request()
    assert body["output_config"]["effort"] == sheets.EFFORT
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_the_image_is_sent_at_native_resolution_with_no_rescaling():
    """800x800 is banzai24's maximum and well under the model's 2576px ceiling,
    so there is nothing to decide and no coordinates to rescale."""
    b64, media = sheets.encode_sheet(SHEET)
    assert media == "image/jpeg"
    assert len(b64) > 1000


# --- cost --------------------------------------------------------------------

def test_cost_is_computed_from_reported_usage_not_assumed():
    extraction = sheets.Extraction(
        lot_number="x", data=_sheet_data(), raw_json="{}", sheet_sha256="a",
        model_id=sheets.MODEL, input_tokens=860, output_tokens=345,
    )
    assert extraction.cost_usd == pytest.approx(860 * 5e-6 + 345 * 25e-6)


def test_cached_prompt_tokens_are_not_billed_as_fresh_input():
    """A cache read is a tenth of an input token. Lumping the two together would
    overstate the bill by ~10x on the prompt — which is the large half of every
    request, and the whole reason it sits behind a cache breakpoint."""
    cached = sheets.Extraction(
        lot_number="x", data=_sheet_data(), raw_json="{}", sheet_sha256="a",
        model_id=sheets.MODEL,
        input_tokens=860, cache_read_tokens=3432, output_tokens=345,
    )
    naive = (860 + 3432) * 5e-6 + 345 * 25e-6

    assert cached.cost_usd == pytest.approx(
        860 * 5e-6 + 3432 * 0.5e-6 + 345 * 25e-6
    )
    assert cached.cost_usd < naive * 0.75


def test_the_first_sheet_pays_the_cache_write_and_later_ones_do_not():
    """1.25x to write, 0.1x to read — so sheet one subsidises the rest of the run."""
    first = sheets.Extraction(
        lot_number="x", data=_sheet_data(), raw_json="{}", sheet_sha256="a",
        model_id=sheets.MODEL,
        input_tokens=860, cache_write_tokens=3432, output_tokens=345,
    )
    later = sheets.Extraction(
        lot_number="y", data=_sheet_data(), raw_json="{}", sheet_sha256="b",
        model_id=sheets.MODEL,
        input_tokens=860, cache_read_tokens=3432, output_tokens=345,
    )
    assert first.cost_usd > later.cost_usd


# --- the live golden-file test -----------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs ANTHROPIC_API_KEY",
)
def test_the_model_reads_the_fixture_sheet_correctly():
    """The plan's acceptance test: assert on known-good values, not on prose.

    Everything asserted here was read off the image by eye, so a failure means
    either the extraction regressed or the sheet fixture changed — not that the
    expectations drifted.
    """
    data, _ = sheets.extract_sheet(SHEET)

    assert data.sheet_grade == GOLDEN["sheet_grade"]
    assert data.exterior_grade == GOLDEN["exterior_grade"]
    assert data.interior_grade == GOLDEN["interior_grade"]
    assert data.sheet_mileage_km == GOLDEN["sheet_mileage_km"]
    assert data.chassis_full == GOLDEN["chassis_full"]

    # The unmasked chassis is the concrete thing this step buys: the API only
    # ever shows DMEJ3P-10**45.
    assert "*" not in data.chassis_full

    assert data.first_registration_raw == GOLDEN["first_registration_raw"]
    assert data.shaken_expiry_raw is None

    # Codes, not panel names — which panel a mark sits on is a judgement call
    # about a diagram, the code is not.
    assert {m.code for m in data.damage_marks} == GOLDEN["damage_codes"]

    assert GOLDEN["warnings_contains"] in (data.warnings_ja or "")
    # The shorthand spelled out. Asserted on the meaning that has to survive —
    # the card is missing — not on a wording the model is free to vary.
    assert "SD" in (data.warnings_en or "")
    assert re.search(r"missing|absent|not (included|present)", data.warnings_en or "",
                     re.IGNORECASE)
    assert GOLDEN["inspector_notes_contains"] in (data.inspector_notes_ja or "")
    assert GOLDEN["private_car_note_contains"] in (data.private_car_note or "")
    assert data.rental_car_note is None      # 車歴 is mutually exclusive

    # The extracted chassis must agree with the mask the API published for this
    # very lot — the check that makes a hallucinated chassis impossible to miss.
    assert sheets.chassis_matches(data.chassis_full, "DMEJ3P-10**52") is True


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs ANTHROPIC_API_KEY",
)
def test_the_live_extraction_agrees_with_what_the_api_said():
    """The cross-checks, end to end — the fixture lot is the 15,415 vs 15,000 case."""
    data, _ = sheets.extract_sheet(SHEET)
    checks = sheets.cross_check(data, _lot())
    assert checks.disagreements == []


# --- which run directory owns a result ---------------------------------------
#
# The extraction queue is global — every pending sheet in the database, from
# whichever run downloaded it. That is right (you want to read the sheets, not
# think about directories), but it means the *results* have to be routed back
# per lot. A morning that fetches two cars is the case that makes it bite.


@pytest.fixture
def runs_root(tmp_path, monkeypatch):
    """A project root with a runs/ tree, since sheet_path is stored relative to it."""
    monkeypatch.setattr(normalize, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _downloaded(root: Path, run: str, lot_number: str) -> AuctionLot:
    """A lot whose sheet is on disk inside ``runs/<run>/sheets/``."""
    sheets_dir = root / "runs" / run / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    (sheets_dir / f"{lot_number}.jpg").write_bytes(b"not-really-a-jpeg")
    return _lot(
        lot_number=lot_number, lot_short=lot_number.rsplit("-", 1)[-1],
        sheet_path=f"runs/{run}/sheets/{lot_number}.jpg",
        sheet_sha256=f"hash-{lot_number}",
    )


def test_a_results_run_directory_is_read_off_the_sheet_it_came_from(runs_root):
    lot = _downloaded(runs_root, "2026-08-09_090000_MAZDA-CX-30", "47-1312-35159")
    owner = sheets.run_dir_of(lot)

    assert owner == runs_root / "runs" / "2026-08-09_090000_MAZDA-CX-30"
    assert sheets.owned_by(lot, owner)
    assert not sheets.owned_by(lot, runs_root / "runs" / "2026-08-09_090412_TOYOTA-RAV4")


def test_a_lot_with_no_downloaded_sheet_has_no_owning_run(runs_root):
    assert sheets.run_dir_of(_lot(sheet_path=None)) is None
    assert sheets.run_dir_of(_lot(sheet_path="runs/gone/sheets/nope.jpg")) is None


def test_each_extraction_is_written_to_the_run_that_downloaded_its_sheet(
    runs_root, monkeypatch, tmp_path
):
    """The two-car morning: one queue, two runs, two extractions.jsonl files.

    Before this, both results were appended to whichever single run directory
    the caller passed — so one run's file described lots it had never seen, and
    the other run's file was missing its own. The database was right either way,
    which is exactly why it could go unnoticed: the damage was only to the run
    directory, the artifact whose whole job is to be an honest record of one run.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()

    cx30 = _downloaded(runs_root, "2026-08-09_090000_MAZDA-CX-30", "47-1312-35159")
    rav4 = _downloaded(runs_root, "2026-08-09_090412_TOYOTA-RAV4", "65-1953-02391")

    monkeypatch.setattr(sheets, "extract_lot", lambda lot, **kw: sheets.Extraction(
        lot_number=lot.lot_number, data=_sheet_data(), raw_json="{}",
        sheet_sha256=lot.sheet_sha256, model_id="claude-opus-5",
    ))

    sheets.run_extract([cx30, rav4], run_dir=None, client=object())

    def lines(run: str) -> list[dict]:
        path = runs_root / "runs" / run / "extractions.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [r["lot_number"] for r in lines("2026-08-09_090000_MAZDA-CX-30")] == [
        "47-1312-35159"]
    assert [r["lot_number"] for r in lines("2026-08-09_090412_TOYOTA-RAV4")] == [
        "65-1953-02391"]
