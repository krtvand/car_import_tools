"""Read a Japanese auction sheet with Claude and return validated fields.

The sheet is the half of a lot that the list API does not expose: sub-grades,
the damage map, shaken, warnings, the inspector's handwritten-style notes, and
the **unmasked** chassis number. banzai24 sells sheet translation as a paid
service; this is a direct substitute for it.

Three things make this cheaper and more reliable than it first looked:

* **The sheet is a digital render, not a photo of paper.** Crisp text at
  800x800 — there is no handwriting-OCR problem to solve.
* **The damage-code legend is printed on every sheet**, so the prompt can state
  it as ground truth instead of asking the model to infer or recall it.
* **800x800 is the maximum banzai24 serves**, well under Claude Opus 5's
  2576px ceiling, so there is no downscaling decision to make and no
  coordinate-scaling maths.

The model returns a validated :class:`SheetData` — a Pydantic schema enforced
server-side via ``client.messages.parse()`` — not prose to be regex'd.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from . import db, normalize
from .models import AuctionLot, SheetExtraction

MODEL = "claude-opus-5"

# Thinking is **on by default** on Claude Opus 5 (unlike Opus 4.8, where
# omitting the parameter meant no thinking), and `max_tokens` caps thinking plus
# response text together. 8000 leaves room for both; the JSON itself is ~400
# tokens, so nearly all of it is headroom against a truncated extraction.
MAX_TOKENS = 8000

# Reading a dense but perfectly legible form is a scoped extraction task, not
# open-ended reasoning — the profile Opus 5 handles well at low effort. `medium`
# is the cautious default because a misread grade or chassis costs more than the
# tokens do; drop to `low` once the golden test confirms accuracy holds, or
# raise if a field starts coming back wrong.
EFFORT = "medium"

# Printed at the foot of every sheet, so this is transcription, not recall.
DAMAGE_CODES = {
    "A": "キズ — scratch",
    "U": "ヘコミ — dent",
    "B": "キズを伴うヘコミ — dent with scratch",
    "P": "要塗装 — needs painting",
    "W": "補修跡 — repair marks",
    "S": "錆 — rust",
    "C": "腐食、穴 — corrosion or hole",
    "G": "フロントガラス点キズ — windscreen chip",
    "XX": "交換済み — already replaced",
    "X": "要交換 — needs replacing",
    "欠": "欠品 — part missing",
}


class DamageMark(BaseModel):
    """One code placed on the car diagram, e.g. ``A1`` on the right rear."""

    panel: str = Field(description="Where on the car diagram the code sits, in "
                                   "English: 'front bumper', 'right rear door', "
                                   "'roof', 'windscreen'. Best effort.")
    code: str = Field(description="The code exactly as printed, e.g. 'A1', 'U2', "
                                  "'XX'. Letter is the damage type, digit is "
                                  "severity 1-4.")


class SheetData(BaseModel):
    """What one auction sheet says. Every field is nullable on purpose.

    A blank box on the sheet is information — no shaken means the car is not
    road-legal and the buyer pays to register it — so ``None`` must mean "the
    sheet did not say", never "the model gave up". The prompt instructs null
    over guessing for exactly this reason.
    """

    sheet_grade: str | None = Field(description="評価点, the overall grade box, "
                                                "top right. '5', '4.5', 'R', 'S'…")
    exterior_grade: str | None = Field(description="外装, exterior sub-grade A-E")
    interior_grade: str | None = Field(description="内装, interior sub-grade A-E")

    sheet_mileage_km: int | None = Field(description="走行, in km, digits only. "
                                                     "'15,415 km' -> 15415")
    chassis_full: str | None = Field(description="車台番号, the full unmasked "
                                                 "chassis number, e.g. 'DMEJ3P-103452'")

    first_registration_raw: str | None = Field(description="初度登録 exactly as "
                                                           "printed, era and all: "
                                                           "'R5年1月'. Do not convert.")
    shaken_expiry_raw: str | None = Field(description="車検 expiry exactly as "
                                                      "printed. Null if the box is "
                                                      "blank — blank means no shaken.")

    damage_marks: list[DamageMark] = Field(description="Every code on the car "
                                                       "diagram. Empty list if the "
                                                       "diagram is clean.")
    equipment: list[str] = Field(description="セールスポイント and 純正装備 items, "
                                             "verbatim. Empty list if none.")

    warnings_ja: str | None = Field(description="注意事項欄 box, verbatim Japanese")
    inspector_notes_ja: str | None = Field(description="検査員記入欄 box, verbatim "
                                                       "Japanese")
    inspector_notes_en: str | None = Field(description="Your English translation of "
                                                       "検査員記入欄. Null if that box "
                                                       "is empty.")

    drivetrain: str | None = Field(description="'2WD', '4WD', 'FF', 'FR' as printed "
                                               "near 型式. Null if not printed — many "
                                               "sheets omit it.")

    rental_car_note: str | None = Field(description="車歴 verbatim if it indicates an "
                                                    "ex-rental (レンタカー, レンタ). "
                                                    "Null otherwise.")
    private_car_note: str | None = Field(description="車歴 verbatim if it indicates "
                                                     "private use (自家用). Null "
                                                     "otherwise.")

    confidence: float = Field(description="0.0-1.0, your confidence that the fields "
                                          "above are transcribed correctly.")


PROMPT = f"""You are reading a Japanese used-car auction sheet (オークションシート).

Transcribe the fields below. This is a transcription task, not an inference
task: report what is printed, and use null for anything absent or illegible.

Field labels to key on:
- 出品番号 lot number · 評価点 overall grade · 外装 exterior · 内装 interior
- 初度登録 first registration (Japanese era — R=令和, H=平成, S=昭和)
- 走行 mileage · 車検 shaken (roadworthiness) expiry · 型式 model code
- 車台番号 chassis number — the sheet prints this in full
- 注意事項欄 warnings · 検査員記入欄 inspector's notes
- セールスポイント selling points · 純正装備 factory equipment
- 車歴 vehicle history — 自家用 private use, レンタカー/レンタ ex-rental,
  教習車 driving school, or company/lease wording

The damage map is a top-down car diagram with codes placed on the panel they
refer to. The legend is printed at the foot of every sheet, so treat it as
ground truth rather than guessing:

{chr(10).join(f"  {code}  {meaning}" for code, meaning in DAMAGE_CODES.items())}

A code is a letter plus a severity digit 1-4 (A1 = light scratch, U2 = a more
significant dent). Report the code exactly as printed and name the panel it sits
on in English, as best you can read the diagram.

Rules:
- **Null over guesses.** A blank 車検 box means the car has no valid shaken —
  that is a real, valuable fact. Do not invent a date to fill the field.
- Copy Japanese text **verbatim** into the _ja fields, including half-width
  katakana (ﾋﾟSD欠品 stays exactly that). Translate only where asked.
- Do not convert the era date. Return 初度登録 as printed; conversion happens
  downstream.
- Mileage is digits only, no separators or units.
- 車歴 is mutually exclusive: set at most one of rental_car_note /
  private_car_note, and leave both null if it says neither."""


# Claude Opus 5 list price, per token. Cached input is the reason the prompt
# lives in `system`: a cache read is a tenth of a fresh input token, and a cache
# write is 1.25x — so the first sheet of a run subsidises every one after it.
PRICE_INPUT = 5e-6
PRICE_CACHE_READ = 0.5e-6      # 0.1x input
PRICE_CACHE_WRITE = 6.25e-6    # 1.25x input, 5-minute TTL
PRICE_OUTPUT = 25e-6


@dataclass
class Extraction:
    """One sheet read, plus what it cost and how it was produced."""

    lot_number: str
    data: SheetData
    raw_json: str
    sheet_sha256: str
    model_id: str
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        """Priced per token class, not by lumping them together.

        Summing cached and uncached input would overstate the bill by ~10x on
        the cached portion — which here is the whole prompt, the large half of
        the request. A cost figure that ignores caching would make the caching
        look pointless.
        """
        return (
            self.input_tokens * PRICE_INPUT
            + self.cache_read_tokens * PRICE_CACHE_READ
            + self.cache_write_tokens * PRICE_CACHE_WRITE
            + self.output_tokens * PRICE_OUTPUT
        )


def encode_sheet(path: Path) -> tuple[str, str]:
    """``(base64, media_type)`` for a saved sheet image."""
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return base64.standard_b64encode(path.read_bytes()).decode("ascii"), media


def _message_params(image_b64: str, media_type: str) -> dict:
    """The request body, shared by the sync and batch paths so they cannot drift.

    The prompt goes in ``system`` with a cache breakpoint rather than in the user
    turn: it is identical for every sheet and comfortably over Claude Opus 5's
    512-token cache minimum, so every sheet after the first reads it at ~10% of
    input price. The image — the only part that varies — goes after it, which is
    what makes the prefix reusable at all.
    """
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": PROMPT,
                    "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": EFFORT},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": media_type,
                                             "data": image_b64}},
                {"type": "text", "text": "Transcribe this auction sheet."},
            ],
        }],
    }


class SheetRefused(RuntimeError):
    """Claude Opus 5's safety classifiers declined the request.

    Vanishingly unlikely for a car auction sheet, but it returns HTTP 200 with
    an empty ``content`` rather than raising, so code that reads the parsed
    output without checking would see a confusing ``None`` instead of a cause.
    """


def extract_sheet(path: Path, client: anthropic.Anthropic | None = None) -> tuple[SheetData, dict]:
    """Read one sheet image. Returns the validated data and the raw response.

    The schema is enforced server-side, so what comes back either validates or
    the model is asked to try again — there is no prose to parse and no
    half-parsed dict to defend against downstream.
    """
    client = client or anthropic.Anthropic()
    image_b64, media_type = encode_sheet(path)

    response = client.messages.parse(
        output_format=SheetData,
        **_message_params(image_b64, media_type),
    )

    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        raise SheetRefused(f"{path.name}: request declined (category={category})")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"{path.name}: hit max_tokens ({MAX_TOKENS}) before finishing — the "
            "extraction is truncated. Raise MAX_TOKENS or lower EFFORT."
        )
    if response.parsed_output is None:
        raise RuntimeError(f"{path.name}: no parsed output (stop_reason="
                           f"{response.stop_reason})")

    # `warnings=False` because the parsed response carries a ParsedTextBlock that
    # does not match the plain content-block union, and Pydantic complains about
    # it on every single sheet. The dump itself is correct — the warning is about
    # the generic, not the data.
    return response.parsed_output, response.model_dump(mode="json", warnings=False)


# --- cross-checks ------------------------------------------------------------
#
# Each is a cheap signal on two things at once: whether the extraction is sound,
# and whether the lot is what the listing claims.


def grade_matches(sheet_grade: str | None, grade_origin: str | None) -> bool | None:
    """Should match exactly. ``None`` when either side is absent.

    A mismatch means one of two things, and both are worth surfacing: the model
    misread the grade box, or the lot was re-listed with a different grade than
    the one the API is reporting.
    """
    if not sheet_grade or not grade_origin:
        return None
    return sheet_grade.strip() == grade_origin.strip()


def mileage_matches(sheet_km: int | None, api_km: int | None) -> bool | None:
    """Compared **rounded to the nearest 1,000**, because the API rounds.

    The saved fixture is the whole argument for this: the sheet says 15,415 and
    the API says 15,000. A naive equality check would flag every lot in the
    database and the signal would be worthless.
    """
    if sheet_km is None or api_km is None:
        return None
    return round(sheet_km / 1000) == round(api_km / 1000)


def registration_matches(
    first_registration_raw: str | None, api_year: int | None, api_month: int | None
) -> bool | None:
    """The sheet's era date against the API's Gregorian one.

    This is what :func:`banzai24.normalize.parse_era_date` was written for — the
    sheet prints ``R5年1月`` and the API says 2023 / 1. The month is only
    compared when both sides have one, since the API leaves it null on most lots.
    """
    parsed = normalize.parse_era_date(first_registration_raw)
    if parsed is None or api_year is None:
        return None
    year, month = parsed
    if year != api_year:
        return False
    if month and api_month:
        return month == api_month
    return True


def chassis_matches(chassis_full: str | None, body_number: str | None) -> bool | None:
    """The unmasked chassis against the API's masked one, position by position.

    The API publishes ``DMEJ3P-10**52`` and the sheet prints ``DMEJ3P-103452``:
    the mask hides two characters and reveals the rest, so every unmasked
    position must agree. This is the strongest check of the four, because the
    chassis is the field with the most value and the least redundancy — nothing
    else would catch a hallucinated one, and a wrong chassis is a wrong car.
    """
    if not chassis_full or not body_number:
        return None
    full, masked = chassis_full.strip().upper(), body_number.strip().upper()
    if len(full) != len(masked):
        return False
    return all(m in ("*", f) for f, m in zip(full, masked))


@dataclass
class CrossCheck:
    """The four comparisons, and whether any of them actively disagree."""

    grade: bool | None = None
    mileage: bool | None = None
    registration: bool | None = None
    chassis: bool | None = None

    @property
    def _named(self) -> tuple[tuple[str, bool | None], ...]:
        return (("grade", self.grade), ("mileage", self.mileage),
                ("registration", self.registration), ("chassis", self.chassis))

    @property
    def disagreements(self) -> list[str]:
        return [name for name, ok in self._named if ok is False]

    def describe(self) -> str:
        if bad := self.disagreements:
            return "MISMATCH: " + ", ".join(bad)
        checked = sum(1 for _, ok in self._named if ok is not None)
        return f"{checked}/4 cross-checks agree" if checked else "nothing to check"


def cross_check(data: SheetData | SheetExtraction, lot: AuctionLot) -> CrossCheck:
    """Compare an extraction against what the list API said about the same lot.

    Takes a fresh :class:`SheetData` or a stored :class:`SheetExtraction` — the
    four fields compared are spelled the same on both, deliberately, so the
    report can re-run the checks over the database months later without
    reconstructing the model's output from ``raw_json``.
    """
    return CrossCheck(
        grade=grade_matches(data.sheet_grade, lot.grade_origin),
        mileage=mileage_matches(data.sheet_mileage_km, lot.mileage_km),
        registration=registration_matches(
            data.first_registration_raw, lot.registration_year, lot.registration_month
        ),
        chassis=chassis_matches(data.chassis_full, lot.body_number),
    )


# --- storage -----------------------------------------------------------------


def to_row(extraction: Extraction) -> dict:
    """Flatten an :class:`Extraction` into :class:`SheetExtraction` columns.

    The list fields are stored as JSON text rather than related tables: nothing
    queries an individual damage mark, and keeping them as one column means a
    re-extraction rewrites the row wholesale instead of reconciling children.
    """
    data = extraction.data
    registration = normalize.parse_era_date(data.first_registration_raw)

    return {
        "lot_number": extraction.lot_number,
        "extracted_at": datetime.now(timezone.utc),
        "model_id": extraction.model_id,
        "sheet_sha256": extraction.sheet_sha256,
        "raw_json": extraction.raw_json,

        "interior_grade": data.interior_grade,
        "exterior_grade": data.exterior_grade,
        "sheet_grade": data.sheet_grade,
        "sheet_mileage_km": data.sheet_mileage_km,
        "chassis_full": data.chassis_full,
        "damage_marks": json.dumps([m.model_dump() for m in data.damage_marks],
                                   ensure_ascii=False),
        "equipment": json.dumps(data.equipment, ensure_ascii=False),
        "warnings_ja": data.warnings_ja,
        "inspector_notes_ja": data.inspector_notes_ja,
        "inspector_notes_en": data.inspector_notes_en,
        "drivetrain": data.drivetrain,
        "rental_car_note": data.rental_car_note,
        "private_car_note": data.private_car_note,
        "confidence": data.confidence,

        "first_registration_raw": data.first_registration_raw,
        "first_registration_year": registration[0] if registration else None,
        "first_registration_month": (registration[1] or None) if registration else None,
        "shaken_expiry_raw": data.shaken_expiry_raw,
    }


@dataclass
class ExtractResult:
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    mismatches: list[str] = None            # "35159: grade, mileage"

    def __post_init__(self):
        self.mismatches = self.mismatches or []

    def summary(self) -> str:
        bits = [f"{self.extracted} sheets extracted"]
        if self.skipped:
            bits.append(f"{self.skipped} already done")
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.cost_usd:
            bits.append(f"~${self.cost_usd:.2f}")
        if self.mismatches:
            bits.append(f"{len(self.mismatches)} cross-check mismatches")
        return ", ".join(bits)


def _sheet_file(lot: AuctionLot) -> Path | None:
    if not lot.sheet_path:
        return None
    path = Path(lot.sheet_path)
    if not path.is_absolute():
        path = normalize.PROJECT_ROOT / path
    return path if path.exists() else None


def extract_lot(
    lot: AuctionLot,
    client: anthropic.Anthropic | None = None,
    force: bool = False,
) -> Extraction | None:
    """Read one lot's sheet, unless that exact image has already been read.

    Idempotency is on the image hash, not the lot: a lot whose sheet was
    re-photographed is a different image and does get read again. ``force``
    overrides — the case for it is a prompt improvement, where the image is
    unchanged but what we can get out of it is not.
    """
    path = _sheet_file(lot)
    if path is None:
        return None
    if not force and db.extraction_is_current(lot.lot_number, lot.sheet_sha256):
        return None

    data, raw = extract_sheet(path, client=client)
    usage = raw.get("usage") or {}

    return Extraction(
        lot_number=lot.lot_number,
        data=data,
        raw_json=json.dumps(raw, ensure_ascii=False, default=str),
        sheet_sha256=lot.sheet_sha256 or "",
        model_id=raw.get("model") or MODEL,
        input_tokens=usage.get("input_tokens") or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
        cache_write_tokens=usage.get("cache_creation_input_tokens") or 0,
        output_tokens=usage.get("output_tokens") or 0,
    )


def run_extract(
    lots: list[AuctionLot] | None = None,
    run_dir: Path | None = None,
    force: bool = False,
    limit: int | None = None,
    client: anthropic.Anthropic | None = None,
) -> ExtractResult:
    """Read every pending sheet, writing results as they land.

    Results are appended to ``<run>/extractions.jsonl`` **and** written to the
    database as each one completes, rather than batched at the end: these cost
    real money, and a crash twenty sheets in should not throw away twenty
    sheets' worth of paid extraction.
    """
    client = client or anthropic.Anthropic()
    lots = lots if lots is not None else db.pending_sheets()
    if limit:
        lots = lots[:limit]

    result = ExtractResult()
    jsonl = (run_dir / "extractions.jsonl") if run_dir else None

    for lot in lots:
        try:
            extraction = extract_lot(lot, client=client, force=force)
        except (SheetRefused, anthropic.APIError, RuntimeError) as exc:
            print(f"  {lot.lot_short}: FAILED — {exc}")
            db.mark_sheet_status(lot.lot_number, "failed")
            result.failed += 1
            continue

        if extraction is None:
            result.skipped += 1
            continue

        row = to_row(extraction)
        db.upsert_extraction(row)
        result.extracted += 1
        result.cost_usd += extraction.cost_usd

        checks = cross_check(extraction.data, lot)
        if bad := checks.disagreements:
            result.mismatches.append(f"{lot.lot_short}: {', '.join(bad)}")

        if jsonl:
            with jsonl.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {k: v for k, v in row.items() if k != "raw_json"},
                    ensure_ascii=False, default=str) + "\n")

        print(f"  {lot.lot_short}: grade {extraction.data.sheet_grade}"
              f" ext {extraction.data.exterior_grade}"
              f" int {extraction.data.interior_grade}"
              f" · {extraction.data.sheet_mileage_km} km"
              f" · {checks.describe()}")

    return result
