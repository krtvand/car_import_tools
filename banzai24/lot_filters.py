"""Filters applied to lots *after* they come back, in this process.

Distinct from :class:`banzai24.config.AuctionFilters`, which is the search the
site itself runs: those become URL parameters and decide what banzai24 sends us.
These decide what we keep, and exist for criteria the site's own search cannot
express. They cost a fetch either way, but they keep the expensive downstream
steps — sheet downloads and paid vision extraction — off lots we do not want.

The criteria point two ways: ``body_model_code`` says which lots to *keep*,
``exclude_colours`` and ``exclude_model_grades`` say which to *drop*. That
decides what a lot the API told us nothing about is worth — see
:meth:`LotFilters.matches`.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


def normalize_model_code(value: str | None) -> str:
    """``"5AA-DMEJ3P"``, ``"DMEJ3P"`` and ``"dmej3p"`` all become ``"DMEJ3P"``.

    Japanese model codes are written as an optional type-designation prefix
    (``5AA``, ``6LA``, ``6BA`` — it encodes the emissions/fuel class) then the
    chassis code proper. banzai24 carries the prefix on some lots and not
    others, for the very same car: this run's data holds both ``5AA-DMEJ3P`` and
    a bare ``DMEJ3P``. The chassis code is what identifies the model, so it is
    the only part either side of a comparison keeps.

    Consequence worth knowing: a pattern that is *only* a prefix (``"5AA"``)
    normalizes to something that can never match, which is correct — the prefix
    is exactly the part that is not dependable.
    """
    if not value:
        return ""
    # The code itself contains no hyphen, so the last segment is the code
    # whether or not a prefix was attached.
    return value.strip().upper().rpartition("-")[2].strip()


def model_code_of(lot: dict) -> str:
    """The lot's chassis code, from whichever field actually carries one."""
    car = lot.get("car") or {}
    for raw in (lot.get("bodyModelCode"), car.get("shortCodeModel")):
        if code := normalize_model_code(raw):
            return code

    # Last resort: `bodyNumber` is `DMEJ3P-10**32` — the same code, but followed
    # by the (masked) serial rather than preceded by a prefix. Opposite
    # convention, so it is split from the other end.
    number = ((lot.get("characteristics") or {}).get("bodyNumber") or "").strip().upper()
    return number.partition("-")[0].strip()


# The colours banzai24 has been seen to return, for reference when writing a
# search. Not validated against: the site is free to add one, and a filter that
# refused to load because of a colour we had not met yet would be worse than a
# filter that simply excludes nothing.
KNOWN_COLOURS = (
    "white", "black", "silver", "gray", "blue", "red", "brown", "beige",
    "green", "other",
)


def normalize_colour(value: str | None) -> str:
    """``"BLUE"``, ``"blue"`` and ``" Blue "`` all become ``"blue"``.

    The API writes the code upper-case; :mod:`banzai24.normalize` stores it
    lower-case, so lower-case is the spelling everything downstream shows and
    the one a search is written in.
    """
    return (value or "").strip().lower()


def colour_of(lot: dict) -> str:
    """The lot's colour, or ``""`` when the API gave none."""
    return normalize_colour((lot.get("characteristics") or {}).get("color"))


def normalize_model_grade(value: str | None) -> list[str]:
    """``" 5d 4wd hybrid g "`` becomes ``["5D", "4WD", "HYBRID", "G"]``.

    Words rather than a string, because the trim line is written in whatever
    order the auction house felt like: this run's data holds ``5D 4WD HYBRID G``,
    ``HYBRID G 4WD`` and a bare ``G 4WD`` for one and the same car. A grade is a
    *word* in that line, so words are what both sides of a comparison keep.
    """
    return (value or "").upper().split()


def model_grade_of(lot: dict) -> list[str]:
    """The lot's Модификация — the trim line — as words.

    ``[]`` when the API gave none, which is common: about one hybrid RAV4 in nine
    is listed as no more than ``4WD``. That is a lot whose grade *nobody has
    stated*, not a lot without one.
    """
    return normalize_model_grade((lot.get("characteristics") or {}).get("modification"))


def _names_grade(line: list[str], grade: list[str]) -> bool:
    """Is ``grade`` written in ``line`` as whole consecutive words?

    Whole words so a one-letter grade means the grade: ``X`` must match
    ``HYBRID X 4WD`` without also matching the ``X`` sitting inside a longer
    word, which is the whole risk of banning a single letter.
    """
    if not grade:
        return False
    return any(line[at:at + len(grade)] == grade
               for at in range(len(line) - len(grade) + 1))


@dataclass(frozen=True)
class LotFilters:
    """Post-fetch criteria. Empty means keep everything.

    ``body_model_code`` is multi-select and matched as a **substring** of the
    normalized code, so ``("DMEJ3P",)`` keeps ``5AA-DMEJ3P`` and ``DMEJ3P`` but
    not ``DMEJ3R``, while a deliberately short ``("DMEJ3",)`` keeps both.

    ``exclude_colours`` is written the other way round: it names colours you do
    not want (``("black", "blue")``), matched **whole** and case-insensitively.
    Whole rather than substring: the vocabulary is a short closed list of exact
    codes (:data:`KNOWN_COLOURS`), so a partial name would only ever be a typo,
    never the deliberate widening that a short ``body_model_code`` is.

    ``exclude_model_grades`` names trim lines you do not want (``("X",)``,
    ``("HYBRID X", "X")``), matched as whole consecutive **words** of the lot's
    Модификация. It is an exclusion rather than the positive list the site's own
    ``modelGrade`` filter would give, because the trim line is written
    inconsistently: asking for ``HYBRID G`` loses every lot listed as ``G 4WD``,
    while banning ``X`` catches all four spellings of an X in this run's data and
    leaves the unstated ones for the report to show you.
    """

    body_model_code: tuple[str, ...] = ()
    exclude_colours: tuple[str, ...] = ()
    exclude_model_grades: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def matches(self, lot: dict) -> bool:
        """A missing value means opposite things to the two criteria.

        A lot with no model code anywhere is **rejected**: nothing has shown it
        is the car asked for. A lot with no colour, or no trim line, is
        **kept**: an exclusion only ever drops what it can positively recognise,
        and dropping the unlabelled ones would quietly narrow the search to lots
        that happened to have the field filled in.
        """
        if self.body_model_code:
            code = model_code_of(lot)
            wanted = (normalize_model_code(w) for w in self.body_model_code)
            if not code or not any(w and w in code for w in wanted):
                return False
        if self.exclude_colours:
            colour = colour_of(lot)
            unwanted = {normalize_colour(c) for c in self.exclude_colours}
            if colour and colour in unwanted:
                return False
        if self.exclude_model_grades:
            line = model_grade_of(lot)
            unwanted = (normalize_model_grade(g) for g in self.exclude_model_grades)
            if line and any(_names_grade(line, grade) for grade in unwanted):
                return False
        return True

    def describe(self) -> str:
        shown = [
            f"{f.name}={value}"
            for f in fields(self)
            if (value := getattr(self, f.name))
        ]
        return ", ".join(shown) or "none"


def split(lots: list[dict], filters: LotFilters) -> tuple[list[dict], list[dict]]:
    """``(kept, rejected)`` — both halves, so a run can report what it dropped."""
    kept, rejected = [], []
    for lot in lots:
        (kept if filters.matches(lot) else rejected).append(lot)
    return kept, rejected
