"""Reading the グレード box on an auction sheet, and saying it in English.

The box holds the **trim** — `G`, `S 4WD`, `Z レザーパッケージ` — and on a
Harrier it is the only machine-readable answer to which of four cars you are
looking at. Nothing in the chassis code says: AXUH80 is every 2WD hybrid Harrier
from the cheapest to the dearest, and the four are about ¥1.5M apart. The
argument is in ``cars/reference/harrier-grades.md``; the table is
``cars/inputs/trims.toml``; this module is the fold that joins them.

**"Trim", not "grade".** In this repo a *grade* is 評価点, the inspector's 1–5
condition score, and :attr:`banzai24.models.SheetExtraction.sheet_grade` already
holds one. The box is labelled グレード and holds neither that nor anything
like it, so it is a trim everywhere in the code and on the page.

The read is deliberately three separate answers rather than one string:

* what the sheet **printed**, verbatim, which is shown whatever else happens;
* the **trim** it matched, or ``None``;
* the **modifiers** printed alongside it — ``4WD``, ``E-Four``, ハイブリッド —
  which are facts about the car but not about which trim it is.

``None`` for the trim is a real answer and not a failure. A 60系 Harrier, a car
with no table in the TOML, an auction house that types something nobody has seen
yet: all of them print their Japanese with no gloss under it. A missing gloss
costs a glance at the scan; a wrong one costs €3,000, which is what a leather Z
read as a plain Z is worth.
"""
from __future__ import annotations

import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TRIMS_PATH = Path(__file__).parent / "inputs" / "trims.toml"


class TrimTableError(ValueError):
    """``trims.toml`` is unreadable or a row is missing a field.

    Raised at load. Unlike a cost book this table cannot make a number wrong —
    the worst a bad row does is gloss a car incorrectly — but it is small,
    hand-written and read on every report, so a typo should surface the first
    time anything asks rather than silently un-glossing one trim for ever.
    """


# Words a グレード box carries beside the trim. They are lifted out before the
# match, not ignored: 4WD is worth money in Japan (the reference doc measures
# the E-Four premium at ¥150,000-odd) and belongs on the card — it just is not
# the answer to *which trim*.
#
# Keyed by the folded spelling, valued by what to show. ハイブリット is a
# misspelling of ハイブリッド and is on a real sheet in this repo (USS 23961);
# it is here because the house that types it will type it again.
MODIFIERS: dict[str, str] = {
    "4WD": "4WD",
    "2WD": "2WD",
    "AWD": "4WD",
    "EFOUR": "E-Four",
    "Eフォー": "E-Four",
    "ハイブリッド": "hybrid",
    "ハイブリット": "hybrid",
    "HYBRID": "hybrid",
    "HV": "hybrid",
    "PHEV": "PHEV",
    "プラグインハイブリッド": "PHEV",
}


def fold(text: str | None) -> str:
    """Both sides of every comparison, folded the same way.

    NFKC first, which is the whole reason this is not the plain ``[^a-z0-9]``
    fold the rest of the repo uses: an auction sheet is typed in whichever width
    the house's software emits, so ``Ｚ`` and ``ﾚｻﾞｰﾊﾟｯｹｰｼﾞ`` have to become
    ``Z`` and ``レザーパッケージ`` before anything can match them. Then
    upper-case, then everything non-alphanumeric dropped — which keeps katakana
    and the ``ー`` inside レザー (a modifier letter, so ``isalnum``) and drops
    spaces, quotes and ``・``.
    """
    normalised = unicodedata.normalize("NFKC", text or "").upper()
    return "".join(ch for ch in normalised if ch.isalnum())


@dataclass(frozen=True)
class Trim:
    """One trim of one car, as the catalogue names it in both languages."""

    car: str          # the `cars.definitions` key, e.g. "toyota-harrier"
    key: str          # stable slug, e.g. "z-leather"
    en: str           # 'Z "Leather Package"'
    ja: tuple[str, ...]   # every spelling seen or expected, verbatim
    rank: int         # the catalogue's price order, cheapest = 1
    note: str | None  # one line on how to tell it apart in a photograph

    @property
    def printed_ja(self) -> str:
        """The first ``ja`` spelling — the catalogue's own, for a label."""
        return self.ja[0]


@dataclass(frozen=True)
class TrimReading:
    """What one グレード box was read as.

    ``printed`` is always the sheet's own text and is what the report shows;
    the rest qualifies it. A reading with ``trim=None`` is not an error and is
    not hidden — it is a box nobody has taught this repo to read yet.
    """

    printed: str
    trim: Trim | None = None
    modifiers: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.trim is not None

    @property
    def en(self) -> str | None:
        """The English name plus whatever modifiers were printed with it."""
        if self.trim is None:
            return None
        return " ".join([self.trim.en, *self.modifiers])

    def describe(self) -> str:
        return f"{self.printed} ({self.en})" if self.trim else self.printed


def _parse(raw: dict, path: Path) -> dict[str, tuple[Trim, ...]]:
    """``{car key: trims, cheapest first}``, validated enough to trust."""
    tables: dict[str, tuple[Trim, ...]] = {}
    for car_key, table in raw.items():
        if not isinstance(table, dict):
            raise TrimTableError(f"{path.name}: [{car_key}] is not a table")
        trims = []
        for index, row in enumerate(table.get("trim", []), start=1):
            for required in ("key", "en", "ja", "rank"):
                if not row.get(required):
                    raise TrimTableError(
                        f"{path.name}: [{car_key}] trim {index} has no {required}"
                    )
            trims.append(Trim(
                car=car_key,
                key=str(row["key"]),
                en=str(row["en"]),
                ja=tuple(str(spelling) for spelling in row["ja"]),
                rank=int(row["rank"]),
                note=row.get("note"),
            ))
        keys = [trim.key for trim in trims]
        if len(set(keys)) != len(keys):
            raise TrimTableError(f"{path.name}: [{car_key}] repeats a trim key")
        tables[car_key] = tuple(sorted(trims, key=lambda trim: trim.rank))
    return tables


@lru_cache(maxsize=None)
def _tables(path: Path = TRIMS_PATH) -> dict[str, tuple[Trim, ...]]:
    with path.open("rb") as handle:
        return _parse(tomllib.load(handle), path)


def for_car(car_key: str, path: Path = TRIMS_PATH) -> tuple[Trim, ...]:
    """Every trim defined for a car, cheapest first. Empty for a car with none."""
    return _tables(path).get(car_key, ())


def read(car_key: str | None, printed: str | None,
         path: Path = TRIMS_PATH) -> TrimReading | None:
    """Read one グレード box. ``None`` only when the box was blank.

    The modifiers come out first, and what is left must **equal** a trim's
    spelling. Equality rather than containment, which is the one rule here worth
    arguing about, because containment is the lenient choice and lenient is the
    wrong direction:

    * ``Zレザーパッケージ`` contains ``Z``, so a contains-match has to be
      ordered longest-first or every leather Z reads as a plain Z — €3,000, in
      the expensive direction, silently;
    * and even ordered, a 60系 box reading ``PROGRESS`` contains both ``G`` and
      ``S``, so it would confidently gloss a car this repo has never priced as a
      trim of a car it has.

    Equality cannot do either. What it costs is a box carrying a word nobody
    listed — ``Z レザーパッケージ 寒冷地`` — which comes back unmatched and
    prints its Japanese with no English under it. That is a glance at the scan
    and a line to add to ``trims.toml``, and the card says which file.
    """
    printed = (printed or "").strip()
    if not printed:
        return None

    remainder, modifiers = _lift_modifiers(fold(printed))
    for trim in for_car(car_key or "", path):
        if any(remainder == fold(spelling) for spelling in trim.ja):
            return TrimReading(printed=printed, trim=trim, modifiers=modifiers)

    return TrimReading(printed=printed)


def _lift_modifiers(folded: str) -> tuple[str, tuple[str, ...]]:
    """``(what is left, the modifiers taken out)``.

    Longest spelling first, because ``プラグインハイブリッド`` contains
    ``ハイブリッド`` and a PHEV is a different car rather than a dearer Harrier.
    """
    found: list[str] = []
    for spelling in sorted(MODIFIERS, key=len, reverse=True):
        needle = fold(spelling)
        if needle and needle in folded:
            folded = folded.replace(needle, "", 1)
            if MODIFIERS[spelling] not in found:
                found.append(MODIFIERS[spelling])
    return folded, tuple(found)
