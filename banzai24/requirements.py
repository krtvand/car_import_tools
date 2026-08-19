"""Whether one lot is the car the search asked for.

A search declares its requirements in three sections, and the section says *who
is able to check it* — not how much it matters:

* ``[site]`` — banzai24 filters these out for us, so a lot that reaches this
  module has already passed them **on the API's word**. The auction sheet then
  re-judges them against its own, better numbers.
* ``[api]`` — checked in :mod:`banzai24.lot_filters` before any sheet is read.
  Rejects never reach a report, so nothing here knows about them.
* ``[sheet]`` — only the auction sheet can answer these.

That difference decides what a missing value means, which is the one subtle rule
in this module:

* A ``[site]`` requirement with nothing on the sheet to check it **passes**. The
  site already enforced it; an unreadable mileage box is not grounds to overturn
  a filter banzai24 already applied.
* A ``[sheet]`` requirement with nothing on the sheet to check it is
  **unknown**. There is no prior authority to fall back on — nobody has looked.

So the sheet can only ever *overturn* a ``[site]`` requirement, never confirm one
it had no opinion about. See ``docs/adr/0001-sheet-outranks-api.md`` for why the
sheet wins when both have a number.

The three groups a lot lands in follow from the checks, worst-first: any failure
makes it :data:`FAILS`, any unknown makes it :data:`UNCONFIRMED`, otherwise
:data:`MEETS`. A failure outranks an unknown deliberately — a ``W2`` on the door
disqualifies the car whether or not the drivetrain box was legible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields

from .config import AuctionFilters

# One requirement's answer.
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# One lot's group. These are the report's primary sort key, so the order they
# are declared in is the order they appear on the page.
MEETS, UNCONFIRMED, FAILS = "meets", "unconfirmed", "fails"

GROUP_ORDER = (MEETS, UNCONFIRMED, FAILS)

GROUP_LABELS = {
    MEETS: "meets all requirements",
    UNCONFIRMED: "unconfirmed",
    FAILS: "fails a requirement",
}

# Deliberately not "approved" / "rejected": this tool did not make a decision,
# it checked a list you wrote. And the middle group is *unconfirmed* rather than
# merely unchecked — it holds both unread sheets and sheets that were read but
# left the field blank, which from your side of the screen are the same problem.
GROUP_BLURBS = {
    MEETS: "the sheet was read and nothing on it disqualifies the car",
    UNCONFIRMED: "no answer yet — the sheet is unread, or the field was blank",
    FAILS: "the sheet was read and something on it disqualifies the car",
}


@dataclass(frozen=True)
class Check:
    """One requirement's verdict on one lot.

    ``detail`` is the sentence printed when this check is why a lot dropped a
    group, so it names the value that broke the rule rather than restating the
    rule: "55,415 km, over 55,000" and not "mileage requirement not met".
    """

    name: str                  # "mileage", "grade", "drivetrain", "damage"
    verdict: str               # PASS | FAIL | UNKNOWN
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL

    @property
    def unknown(self) -> bool:
        return self.verdict == UNKNOWN


@dataclass(frozen=True)
class SheetRequirements:
    """The ``[sheet]`` section: what only the auction sheet can answer.

    ``no_damage_codes`` is named for what it asserts rather than what it lists,
    so the report can render "no W/X/欠 marks" straight from the key.

    Matching is on **letters contained anywhere in the code**, never on equality.
    Codes combine — ``auction.db`` holds ``A3U2`` and ``AU1`` — so a literal
    ``"W1"`` would miss a ``W`` sitting inside a compound mark. It also means
    banning ``X`` catches ``XX``, and that the severity digit is ignored: ``W3``
    is a *worse* repair mark than ``W2``, so a ban list naming the digit would
    wave through exactly the marks you most want to see.
    """

    drivetrain: str | None = None
    no_damage_codes: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def describe(self) -> str:
        shown = []
        if self.drivetrain:
            shown.append(f"drivetrain={self.drivetrain}")
        if self.no_damage_codes:
            shown.append(f"no damage codes {'/'.join(self.no_damage_codes)}")
        return ", ".join(shown) or "none"


@dataclass(frozen=True)
class Assessment:
    """Every requirement's verdict on one lot, and the group they add up to."""

    checks: tuple[Check, ...] = ()

    @property
    def group(self) -> str:
        if any(check.failed for check in self.checks):
            return FAILS
        if any(check.unknown for check in self.checks):
            return UNCONFIRMED
        return MEETS

    @property
    def label(self) -> str:
        return GROUP_LABELS[self.group]

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.failed]

    @property
    def unknowns(self) -> list[Check]:
        return [check for check in self.checks if check.unknown]

    def get(self, name: str) -> Check | None:
        """One check by name, so the template can print it against its own value."""
        for check in self.checks:
            if check.name == name:
                return check
        return None

    def describe(self) -> str:
        """The one-line reason a card is in the group it is in."""
        if reasons := [c.detail or c.name for c in self.failures]:
            return "; ".join(reasons)
        if reasons := [c.detail or c.name for c in self.unknowns]:
            return "; ".join(reasons)
        return GROUP_LABELS[MEETS]


# --- comparing single values -------------------------------------------------


def _same_grade(sheet: str | None, wanted: str) -> bool:
    """``"4.5"`` matches ``"4.50"``; ``"R"`` matches ``"r"``.

    Grades are numeric for most of the scale and letters at the ends (``R``,
    ``RA``, ``S``), so neither a float compare nor a string compare covers it
    alone.
    """
    left, right = (sheet or "").strip(), wanted.strip()
    try:
        return float(left) == float(right)
    except ValueError:
        return left.casefold() == right.casefold()


def banned_marks(damage_marks: str | None, codes: tuple[str, ...]) -> list[dict]:
    """Every mark whose code contains one of ``codes``.

    Takes the stored JSON text rather than a parsed list so a malformed row
    cannot raise here: a report that fails to render because one extraction
    holds bad JSON is worse than one that treats it as no marks. That is the
    same trade :func:`banzai24.report._json_list` makes, for the same reason —
    but note the consequence, which is that unreadable damage JSON reads as a
    *clean* diagram rather than an unknown one.
    """
    if not damage_marks or not codes:
        return []
    try:
        marks = json.loads(damage_marks)
    except ValueError:
        return []
    if not isinstance(marks, list):
        return []

    wanted = [code.strip().upper() for code in codes if code.strip()]
    hits = []
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        code = str(mark.get("code") or "").upper()
        if any(banned in code for banned in wanted):
            hits.append(mark)
    return hits


# --- the checks --------------------------------------------------------------


def _bounds_check(name: str, value, low, high, unit: str = "") -> Check:
    """One numeric requirement re-judged against the sheet's own figure."""
    if value is None:
        # The site already enforced this bound; the sheet simply has nothing to
        # overturn it with. See the module docstring.
        return Check(name, PASS)
    shown = f"{value:,}{unit}"
    if low is not None and value < low:
        return Check(name, FAIL, f"{shown}, under {low:,}{unit}")
    if high is not None and value > high:
        return Check(name, FAIL, f"{shown}, over {high:,}{unit}")
    return Check(name, PASS)


def _site_checks(filters: AuctionFilters, lot, extraction) -> list[Check]:
    """The ``[site]`` requirements, re-judged on the sheet where it has a value.

    Only the three the sheet actually carries. Make, model, transmission and
    engine capacity are not on the sheet in a form worth parsing, so banzai24's
    word on them stands unchallenged — which is fine: they are the filters least
    likely to be wrong, and a filter nothing can overturn is simply never a
    reason a lot changes group.
    """
    checks = []

    if filters.mileage_start is not None or filters.mileage_end is not None:
        checks.append(_bounds_check(
            "mileage",
            extraction.sheet_mileage_km if extraction else None,
            filters.mileage_start, filters.mileage_end, " km",
        ))

    if filters.year_start is not None or filters.year_end is not None:
        checks.append(_bounds_check(
            "year",
            extraction.first_registration_year if extraction else None,
            filters.year_start, filters.year_end,
        ))

    if filters.grade_origin:
        grade = extraction.sheet_grade if extraction else None
        # Two ways to pass, for one reason: either the sheet agrees, or it has
        # no opinion and banzai24's own filter therefore stands.
        if not grade or any(_same_grade(grade, w) for w in filters.grade_origin):
            checks.append(Check("grade", PASS))
        else:
            wanted = "/".join(filters.grade_origin)
            checks.append(Check("grade", FAIL, f"grade {grade}, wanted {wanted}"))

    return checks


def _sheet_checks(requirements: SheetRequirements, extraction) -> list[Check]:
    """The ``[sheet]`` requirements. A blank field here is UNKNOWN, not a pass."""
    checks = []

    if requirements.drivetrain:
        actual = (extraction.drivetrain if extraction else None) or ""
        if not actual.strip():
            checks.append(Check("drivetrain", UNKNOWN, "drivetrain not on the sheet"))
        elif actual.strip().casefold() == requirements.drivetrain.strip().casefold():
            checks.append(Check("drivetrain", PASS))
        else:
            checks.append(Check(
                "drivetrain", FAIL,
                f"{actual.strip()}, wanted {requirements.drivetrain}",
            ))

    if requirements.no_damage_codes:
        banned = "/".join(requirements.no_damage_codes)
        if extraction is None:
            checks.append(Check("damage", UNKNOWN, "sheet not read"))
        elif hits := banned_marks(extraction.damage_marks, requirements.no_damage_codes):
            shown = ", ".join(
                f"{hit.get('code')}" + (f" on the {hit['panel']}" if hit.get("panel") else "")
                for hit in hits
            )
            checks.append(Check("damage", FAIL, f"{shown} — wanted no {banned} marks"))
        else:
            checks.append(Check("damage", PASS))

    return checks


def judge(
    filters: AuctionFilters,
    requirements: SheetRequirements,
    lot,
    extraction,
) -> Assessment:
    """Every requirement's verdict on one lot.

    ``extraction`` is ``None`` for a lot whose sheet has not been read, which is
    the majority of a freshly fetched run — and which is exactly why the middle
    group exists rather than these lots being quietly counted as passing.
    """
    return Assessment(tuple(
        _site_checks(filters, lot, extraction)
        + _sheet_checks(requirements, extraction)
    ))
