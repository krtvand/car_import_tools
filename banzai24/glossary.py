"""Japanese sheet terms, glossed into English once and kept for ever.

The report prints two lists a non-Japanese reader cannot use as they stand: the
装備 equipment items and the 注意事項欄 warnings box. Both are typed in auction
shorthand — ``ｴｱB``, ``純正AW``, ``ﾋﾟSD欠品`` — and both **repeat**. Across the
202 extractions in ``auction.db`` there are about 600 distinct terms and several
thousand uses of them: ``PS`` and ``PW`` are on nearly every sheet, ``★オーク
ションデビュー★`` on a quarter of them.

That ratio is the whole design. A gloss is a property of the *term*, not of the
lot, so it is worth paying for exactly once:

* every term is translated by Claude **the first time it is ever seen**, and
  written to ``inputs/glossary.json``;
* every later sheet that prints it — this morning's or next year's — reads the
  file and costs nothing;
* and because the answer comes from the file rather than from a fresh model
  call, ``純正ナビ`` is glossed the same way on every card of every report,
  which a per-sheet translation could not promise.

The file is the artefact, not a cache: it is committed, hand-editable, and a
correction typed into it is permanent. ``null`` is a real entry — a term the
model could not read is recorded as unglossable so it is never paid for twice.

**The report never calls the API.** It looks terms up and prints the Japanese
alone when there is no gloss, which keeps ``report`` what it claims to be: free
to re-run, offline, deterministic. Filling the glossary is the job of
``extract`` (which does it for each sheet it reads) and of the ``glossary``
command (which backfills the database).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

PATH = Path(__file__).parent / "inputs" / "glossary.json"

MODEL = "claude-opus-5"

# Deliberately the same effort as the sheet read. Glossing looks like the
# easier job — it is a page of two-word phrases — but the phrases are the
# opaque half of the sheet: ``ﾋﾟSD欠品`` is "navi SD card missing", and reading
# it wrong is the difference between a car that needs a €300 part and one that
# does not. It is paid for once per term for ever, so there is nothing to save.
EFFORT = "medium"

# Room for a batch of 60 glosses (~1,500 tokens) plus the thinking that Claude
# Opus 5 does by default. Nowhere near the cap in practice; it is here so a
# batch cannot come back truncated and lose the terms that did not fit.
MAX_TOKENS = 8000

# Terms per request. Small enough that a failure loses little and the output
# cannot approach MAX_TOKENS, large enough that a first backfill of ~600 terms
# is ten calls rather than six hundred.
BATCH = 60

# Printed around a term but not part of it: the houses decorate their selling
# points and the bullet is punctuation, not a word. Stripped for the *key* only
# — the report prints what the sheet printed, decorations and all.
DECORATION = "★☆●○◎■□◆◇・※＊*+-–—〜~ 　\t\r\n!！?？。、，,．:：;；"


def key(term: str | None) -> str:
    """The glossary key for a printed term: NFKC-folded and undecorated.

    NFKC is what makes the file small and consistent. An auction house types in
    whichever width its software emits, so ``ｴｱB`` and ``エアB``, ``ＳＤ`` and
    ``SD`` are the same word arriving twice; folding them together means one
    entry, one translation, and one price paid. Case is **not** folded — ``PS``
    is power steering and ``ps`` is nothing.

    Returns ``""`` for anything with no letters or digits left in it, which is
    how stray brackets and lone bullets stay out of the file.
    """
    folded = unicodedata.normalize("NFKC", term or "").strip()
    folded = folded.strip(DECORATION).strip()
    return folded if any(ch.isalnum() for ch in folded) else ""


# Whitespace only — every width of it, plus line breaks. The 注意事項欄 is a
# free-text box, and this is the one separator its writers agree on:
# `取保　スペアキー　後送` is three facts, `デジタルインナーミラーＳＤ欠品\n
# ナビなし` is two.
#
# Not `、`, deliberately, and not `・`. Both appear *inside* items as often as
# between them — `R5年2月14日走行18380km時、メーター交換` is one sentence with a
# comma in it, and splitting there would file half a sentence in the glossary
# for ever. Whitespace over-splits a Latin phrase now and then (`＊Apple
# CarPlay対応` becomes two items, glossed "Apple" and "CarPlay compatible"),
# which reads fine on the card; splitting on punctuation would mangle meaning,
# which does not.
_SEPARATOR = re.compile(r"[\s　]+")


def split_warnings(text: str | None) -> list[str]:
    """The 注意事項欄 box as the items it is written in, verbatim.

    Equipment arrives already split — the extraction returns it as a list — but
    the warnings box is one string holding anything from a single word to three
    facts and a sentence, and the report needs the items to gloss them one by
    one.

    An item with no letters or digits in it is dropped. Sheets bracket a group
    of items — ``（保取 スペアキー ナビSD 後日）`` — and splitting on the spaces
    inside leaves the two brackets stranded; a line on the card reading ``（``
    says nothing the line under it does not, and the group's items still print
    in order.
    """
    return [item for item in _SEPARATOR.split(text or "") if key(item)]


# --- the file ----------------------------------------------------------------


def load(path: Path | None = None) -> dict[str, str | None]:
    """``{key: gloss or None}``. An unreadable or missing file is an empty one.

    Never raises. The glossary is a convenience over the Japanese, which is
    always printed anyway, so a corrupt file must cost glosses and not the
    report.

    ``path`` resolves to :data:`PATH` at call time rather than as a default
    argument, so pointing the module at another file — a test's, a second
    operator's — is one assignment and every function follows.
    """
    path = path or PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): (str(v) if v is not None else None) for k, v in data.items()}


def save(glossary: dict[str, str | None], path: Path | None = None) -> None:
    """Write it back, sorted, one term per line.

    Sorted and indented because this file is read by people and diffed by git:
    a run that learns three terms should show three added lines, not a
    reshuffled blob.
    """
    path = path or PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(sorted(glossary.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def look_up(term: str | None, glossary: dict[str, str | None] | None = None) -> str | None:
    """The English for one printed term, or ``None`` if there is none to give.

    ``None`` covers both "nobody has translated this yet" and "the model looked
    and could not read it". The card cannot act differently on the two, and
    pretending otherwise would put a second empty state on the page.
    """
    glossary = load() if glossary is None else glossary
    return glossary.get(key(term))


def gloss(terms: list[str], glossary: dict[str, str | None] | None = None) -> list[dict]:
    """``[{ja, en}, …]`` — what the sheet printed, and its English beside it.

    The shape the template wants: one row per item, ``en`` possibly ``None``.
    """
    glossary = load() if glossary is None else glossary
    return [{"ja": term, "en": look_up(term, glossary)} for term in terms]


def missing(terms: list[str], glossary: dict[str, str | None] | None = None) -> list[str]:
    """The keys in ``terms`` this glossary has never been asked about.

    Order-preserving and de-duplicated, so a batch reads like the sheet it came
    from. A term already recorded as ``null`` is **not** missing — that question
    has been asked and answered.
    """
    glossary = load() if glossary is None else glossary
    seen, unknown = set(), []
    for term in terms:
        folded = key(term)
        if folded and folded not in glossary and folded not in seen:
            seen.add(folded)
            unknown.append(folded)
    return unknown


# --- asking Claude -----------------------------------------------------------


class Gloss(BaseModel):
    """One term and its English, as the model returns it."""

    ja: str = Field(description="The Japanese term, copied back exactly as given")
    en: str | None = Field(description="Short English gloss, or null if the term "
                                       "cannot be read with any confidence")


class Glossary(BaseModel):
    terms: list[Gloss]


PROMPT = """\
You are glossing shorthand from Japanese used-car auction sheets (中古車オーク
ション出品票) into English for a European importer reading the sheet in
translation.

The terms come from two boxes:

* the equipment and selling-point lines — 装備, セールスポイント, 純正装備 —
  which list fitted options in heavy abbreviation: ナビ, ｴｱB, PS, PW, 純正AW,
  SR, カワ, ワンオーナー;
* the 注意事項欄 warnings box, where the inspector notes what is wrong,
  missing, or being sent separately: SD欠品, 取保, 後送, スペアキー.

For each term give a **short English gloss**, not a sentence: what a parts
catalogue or a condition report would call it. Six words is long.

* Spell out the abbreviation rather than transliterating it. ｴｱB is "airbag",
  not "eaB". PS is "power steering". カワ is "leather seats". 純正 is
  "factory-fitted" or "genuine", never "pure".
* Keep an English or numeric part that is already meaningful: 19インチAW is
  "19-inch alloy wheels", ETC2.0 is "ETC 2.0 toll transponder".
* Say what a warning **means for the buyer**. 欠品 is "missing", not "shortage"
  — ﾋﾟSD欠品 is "navigation SD card missing". 後送 is "to be sent later". 取保
  is "owner's manual and service book present".
* Marketing decoration is still a term: ★オークションデビュー★ is "first time
  at auction".
* If a term is genuinely unreadable — a typo, a house's private code, a
  fragment — return null for it. A missing gloss costs a glance at the scan; an
  invented one is read as fact and priced.

Copy each `ja` back exactly as it was given to you, so the answers can be
matched to the questions. Return one entry per term, in the order given.
"""


def translate(terms: list[str], client: anthropic.Anthropic | None = None) -> dict[str, str | None]:
    """Ask Claude for the English of terms nobody has glossed yet.

    One request per :data:`BATCH` terms. The prompt is cached — it is the same
    every time and comfortably over the 512-token minimum — so a backfill of ten
    batches pays for it once.

    Answers are matched back **by key**, not by position: a model that dropped
    or reordered an entry would otherwise silently file every gloss against the
    wrong term, which is the one failure this module must not have.
    """
    if not terms:
        return {}
    client = client or anthropic.Anthropic()
    glosses: dict[str, str | None] = {}

    for start in range(0, len(terms), BATCH):
        batch = terms[start:start + BATCH]
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT},
            output_format=Glossary,
            messages=[{"role": "user", "content": "\n".join(batch)}],
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(
                f"glossary: no answer for {len(batch)} terms "
                f"(stop_reason={response.stop_reason})"
            )
        wanted = set(batch)
        for entry in response.parsed_output.terms:
            folded = key(entry.ja)
            if folded in wanted:
                glosses[folded] = (entry.en or "").strip() or None

    return glosses


def ensure(terms: list[str], client: anthropic.Anthropic | None = None,
           path: Path | None = None) -> dict[str, str | None]:
    """Gloss whatever in ``terms`` is new, add it to the file, return what was added.

    The entry point for "we have just met some words". Costs nothing — makes no
    request at all — when every term is already known, which after the first few
    runs is the usual case: this is why the extraction can call it per sheet
    without turning one paid request into two.

    The file is re-read immediately before writing so a concurrent run's terms
    are not dropped.

    A term the model *answered with* ``null`` is written as ``null`` — asked and
    answered, never paid for again. A term it simply left out of its answer is
    **not** written, so the next run asks about it again. The two look identical
    in the returned list and are worlds apart: 欠品 is perfectly readable, and
    recording it as unreadable because one batch came back a line short would
    blank that gloss on every card for ever. An omission costs a few tokens next
    time; a wrong ``null`` is permanent.
    """
    path = path or PATH
    glossary = load(path)
    unknown = missing(terms, glossary)
    if not unknown:
        return {}

    learned = translate(unknown, client=client)
    if not learned:
        return {}

    fresh = load(path)
    fresh.update(learned)
    save(fresh, path)
    return learned


def terms_of(equipment: list[str] | str | None, warnings_ja: str | None) -> list[str]:
    """Every term one sheet contributes: its equipment list and its warnings box.

    One place decides what is glossable, so the extraction, the backfill command
    and the report cannot disagree about it — a term the report looks up but the
    extraction never learned would be a permanently blank gloss.

    ``equipment`` is taken either as the list a fresh extraction holds or as the
    JSON text the database column stores, because both callers are real and
    neither should have to know which the other passes. Malformed JSON yields no
    equipment terms rather than an error: the warnings half of the sheet is
    still worth glossing.
    """
    if isinstance(equipment, str):
        try:
            equipment = json.loads(equipment)
        except ValueError:
            equipment = []
    if not isinstance(equipment, list):
        equipment = []
    return [str(item) for item in equipment] + split_warnings(warnings_ja)
