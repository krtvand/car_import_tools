"""Join a run directory and the database into one browsable ``report.html``.

**Read-only, and self-contained.** The sheet images are inlined as data URIs and
the CSS is inline, so the file opens by double-click and keeps working after the
run directory is copied to another machine, mailed, or archived. Nothing here
writes to the database and nothing here touches the network.

This is the step a spreadsheet cannot do: *the sheet scan next to the fields
read off it*. Grade 4.5 with an `A1` on the roof means nothing without the
picture — you need to see how big the scratch is drawn.

Three sources meet here, and each answers something the others cannot:

* the **run directory** says which lots this run is about (``lots.json``);
* **auction.db** holds what is known about them across every run — the API
  fields and the paid extraction;
* **bazaraki.db** holds the Cyprus asking prices, which is the only thing that
  turns a grade and a mileage into a decision about money;
* the **bid tables** under ``inputs/`` turn that into the number you type into
  the bidding platform — see :mod:`banzai24.bidding`.

Regenerating is free — no network, no browser, no model call — so a template
tweak is a re-run of ``report``, never a re-fetch or a re-extract.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import db, normalize, search, sheets
from .bidding import BidPricer, BidQuote
from .models import AuctionLot, SheetExtraction
from .requirements import (
    GROUP_BLURBS,
    GROUP_LABELS,
    GROUP_ORDER,
    Assessment,
    judge,
)
from .search import SearchDefinition
from .sheets import CrossCheck

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Below this, the model itself is telling you it struggled with the sheet — a
# reason to look at the scan rather than trust the transcription. 0.9 rather
# than something lower because the cost of looking is seconds and the cost of a
# misread grade is a bad bid; the real fixture extraction came back at 0.83.
LOW_CONFIDENCE = 0.9


# --- flags -------------------------------------------------------------------
#
# Sorting and flagging carry this report. Twenty clean lots and three that need
# a second look is the normal shape of a run, and the three must not be buried
# behind the twenty. Severity is the sort key, so the ordering is a consequence
# of what each flag means rather than a separate decision to keep in step.


@dataclass(frozen=True)
class Flag:
    key: str        # CSS class + stable name
    label: str      # what the badge says
    severity: int   # higher sorts first


# Severities, named because they are read twice: once to sort within a group, and
# once to decide what counts as "flagged" in the header.
#
# There is no longer a ``not-read`` flag: the *unconfirmed* group says that now,
# for every lot in it, and a badge repeating it on each card was the same fact
# printed twice.
#
# ``bid_reduced`` deliberately flags nothing and changes no ordering. It is a
# number to read off the card you are already looking at, not a finding — and a
# report that re-sorted itself every time the price table was re-tuned would stop
# being the stable page you scroll.
MISMATCH, LOW_CONFIDENCE_SEV = 50, 30
NEEDS_EYES = LOW_CONFIDENCE_SEV

# Cross-checks that are **not** requirements, and so still earn a badge. Grade
# and mileage disagreements moved out: those two are re-judged as requirements
# now (``docs/adr/0001-sheet-outranks-api.md``), and a lot that fails one is
# already sitting in the *fails a requirement* group with the exact figure
# printed against the value it broke. A badge saying the same thing in a second
# vocabulary is how a page stops being read.
STRUCTURAL_CHECKS = ("chassis", "registration")


def _flags(
    extraction: SheetExtraction | None,
    checks: CrossCheck | None,
) -> list[Flag]:
    """Every reason this lot wants your attention, most urgent first.

    Deliberately not deduplicated into one "needs review" boolean: *why* a lot
    is flagged decides what you do about it. A chassis mismatch is a different
    car; a low confidence score is a legible-sheet problem.
    """
    flags = []

    if checks and (bad := [name for name in checks.disagreements
                           if name in STRUCTURAL_CHECKS]):
        # The API and the sheet disagree about a fact both claim to know, and one
        # no requirement tests: this may not be the car the listing describes.
        # It sits at the top of whichever group the lot is in, including *meets
        # all requirements* — which is uncomfortable, and correct.
        flags.append(Flag("mismatch", f"{', '.join(bad)} mismatch", MISMATCH))

    if extraction and extraction.confidence is not None and extraction.confidence < LOW_CONFIDENCE:
        flags.append(Flag("low-confidence", f"confidence {extraction.confidence:.2f}",
                          LOW_CONFIDENCE_SEV))

    # A blank 車検 box is deliberately not flagged. It is a real cost — the buyer
    # pays to put the car back on the road — but it is also the common case on
    # export lots, so a badge for it fired on most of the page and crowded out
    # the findings that are actually unusual. The fact still shows in the card,
    # against the 車検 field where the price of it is read off.

    return flags


# --- Cyprus comparables ------------------------------------------------------


@dataclass(frozen=True)
class CyprusComp:
    """What the same car is being asked for in Cyprus, from ``bazaraki.db``."""

    median: float | None
    n: int
    confidence: str
    year_tol: int
    mileage_band: int

    def describe(self) -> str:
        if self.median is None:
            return "no Cyprus comparables"
        band = f"±{self.year_tol}y ±{self.mileage_band // 1000}k km"
        return f"€{self.median:,.0f} · n={self.n} · {self.confidence} · {band}"


class CyprusPricer:
    """Median Cyprus asking price for a lot's make/model/year/mileage.

    The two databases are joined on nothing but the make and model strings, so
    :func:`bazaraki.analysis.filter_model` does the matching — it normalises
    case and punctuation, which is exactly the gap between banzai24's ``MAZDA``
    / ``CX-30`` and bazaraki's ``Mazda`` / ``CX-30``.

    Listings are loaded once per report and the per-model subsets are cached:
    a run is usually one model, so this is one query and one filter no matter
    how many lots it holds.

    An unavailable or empty ``bazaraki.db`` is not an error. The Cyprus number
    is context, not a prerequisite — a report without it is still the sheet next
    to the fields, which is the point of the page.
    """

    def __init__(self, records=None):
        self._analysis = None
        self._records = records
        self._by_model: dict[tuple[str, str], list] = {}
        self.available = records is not None
        self.reason: str | None = None if records is not None else "not loaded"

        if records is None:
            try:
                from bazaraki import analysis, db as bazaraki_db

                self._analysis = analysis
                self._records = analysis.to_records(bazaraki_db.all_listings())
                self.available = True
                self.reason = None
            except Exception as exc:  # missing db, missing package, unreadable file
                self.reason = f"{type(exc).__name__}: {exc}"
                return

        if self._analysis is None:
            from bazaraki import analysis

            self._analysis = analysis

    def for_lot(self, lot: AuctionLot) -> CyprusComp | None:
        """``None`` when there is nothing to compare on — no data, or no query.

        A lot missing its year or mileage has no query to ask, which is a
        different thing from asking and finding nothing; the second returns a
        :class:`CyprusComp` with ``median=None`` so the report can say "we
        looked".
        """
        if not self.available or not lot.mark or not lot.model:
            return None
        if lot.registration_year is None or lot.mileage_km is None:
            return None

        key = (lot.mark, lot.model)
        if key not in self._by_model:
            scoped = self._analysis.filter_model(self._records, lot.mark, lot.model)
            self._by_model[key] = self._analysis.clean(scoped)

        comp = self._analysis.comparables(
            self._by_model[key], lot.registration_year, lot.mileage_km
        )
        return CyprusComp(
            median=comp.estimate,
            n=comp.n,
            confidence=comp.confidence,
            year_tol=comp.year_tol,
            mileage_band=comp.mileage_band,
        )


# --- one lot, ready to render ------------------------------------------------


def _data_uri(path: Path | None) -> str | None:
    """A sheet image as ``data:image/jpeg;base64,…``.

    Inlining rather than linking is what makes the report survive being copied:
    a ``<img src="sheets/47-1312-35159.jpg">`` breaks the moment the HTML is
    moved out of its run directory, and a link back to banzai24's image service
    would need the network and would rot when they rotate the token.
    """
    if path is None or not path.exists():
        return None
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{media};base64,{base64.standard_b64encode(path.read_bytes()).decode('ascii')}"


def _sheet_file(lot: AuctionLot) -> Path | None:
    """Resolve ``sheet_path`` — stored relative to the project root — to a file."""
    if not lot.sheet_path:
        return None
    path = Path(lot.sheet_path)
    if not path.is_absolute():
        path = normalize.PROJECT_ROOT / path
    return path if path.exists() else None


def _json_list(text: str | None) -> list:
    """``damage_marks`` / ``equipment`` are stored as JSON text. Never raise here.

    A report that fails to render because one row holds malformed JSON is worse
    than a report with one empty equipment list.
    """
    if not text:
        return []
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


@dataclass
class LotView:
    """One lot with everything known about it, prepared for the template.

    The formatting lives here rather than in the template so it can be tested
    without rendering, and so the template stays a layout rather than a second
    place where business rules hide.
    """

    lot: AuctionLot
    extraction: SheetExtraction | None = None
    checks: CrossCheck | None = None
    comp: CyprusComp | None = None
    quote: BidQuote | None = None      # None only when a bid table is missing
    flags: list[Flag] = field(default_factory=list)
    sheet_uri: str | None = None
    assessment: Assessment | None = None   # None when the run named no search
    requirements: object | None = None     # the [sheet] section, for the card

    @property
    def group(self) -> str | None:
        return self.assessment.group if self.assessment else None

    @property
    def sort_key(self) -> tuple:
        """Group first, then flagged lots, then in the order they cross the block.

        The group is the primary key because it is the question you are asking
        the page: *what can I bid on this morning*. Severity sorts within it, so
        a possible wrong car still rises to the top of whichever group it is in.
        Within a severity band the trade time is the tiebreak, because that is
        the order you will actually have to make decisions in.
        """
        rank = GROUP_ORDER.index(self.group) if self.group else 0
        severity = max((flag.severity for flag in self.flags), default=0)
        return (
            rank,
            -severity,
            str(self.lot.trade_date or ""),
            self.lot.trade_time or "",
            self.lot.lot_number,
        )

    def check(self, name: str):
        """One requirement's verdict, so the template can print it in place.

        Returns ``None`` when the search does not test that field at all, which
        the template renders as no marker rather than as a pass — a car nobody
        asked a question about has not answered one.
        """
        return self.assessment.get(name) if self.assessment else None

    @property
    def failures(self) -> list:
        return self.assessment.failures if self.assessment else []

    @property
    def unknowns(self) -> list:
        return self.assessment.unknowns if self.assessment else []

    @property
    def verdict_line(self) -> str | None:
        """The one line saying why this card is not in the top group."""
        if not self.assessment or self.assessment.group == GROUP_ORDER[0]:
            return None
        return self.assessment.describe()

    @property
    def title(self) -> str:
        bits = [self.lot.mark, self.lot.model, self.lot.modification]
        return " ".join(b for b in bits if b) or self.lot.lot_number

    @property
    def registration(self) -> str | None:
        if self.lot.registration_year is None:
            return None
        if self.lot.registration_month:
            return f"{self.lot.registration_year}-{self.lot.registration_month:02d}"
        return str(self.lot.registration_year)

    @property
    def banned_codes(self) -> tuple[str, ...]:
        """The ``no_damage_codes`` this lot was judged against, if any."""
        return getattr(self.requirements, "no_damage_codes", ()) or ()

    @property
    def damage_marks(self) -> list[dict]:
        """``[{panel, code, meaning, banned}]`` — the legend joined in at render.

        ``meaning`` is ``None`` for a code the legend does not cover. That
        happens for real: one extraction returned ``トビA`` (a stone chip),
        which the prompt correctly passed through verbatim rather than forcing
        into a known letter.

        ``banned`` marks the codes that put this lot in *fails a requirement*,
        so the card shows *which* mark disqualified it rather than a list you
        have to re-scan against the rule yourself.
        """
        # The same containment test :func:`requirements.banned_marks` runs, applied
        # here per mark so the card can point at the offending one. Kept as one
        # expression rather than a call into that function because what is wanted
        # here is a flag per mark, not the subset.
        wanted = [code.strip().upper() for code in self.banned_codes if code.strip()]
        marks = []
        for mark in _json_list(self.extraction.damage_marks if self.extraction else None):
            if not isinstance(mark, dict):
                continue
            code = str(mark.get("code") or "")
            marks.append({
                "panel": mark.get("panel") or "",
                "code": code,
                "meaning": sheets.DAMAGE_CODES.get(code[:1]),
                "banned": any(b in code.upper() for b in wanted),
            })
        return marks

    @property
    def history_note(self) -> dict | None:
        """``{ja, en, rental, unset}`` for 車歴 — always shown once a sheet is read.

        The two nullable notes collapse to one line here because the card shows
        one line; ``rental`` keeps the distinction the colour depends on.

        A sheet that says neither renders as *unset* rather than as a missing
        row. It is not a null worth hiding: it is the input the bid falls back
        to ``private`` on, and the card has to show what that assumption was
        made from.
        """
        if self.extraction is None:
            return None
        ja = self.extraction.rental_car_note or self.extraction.private_car_note
        if not ja:
            return {"ja": None, "en": None, "rental": False, "unset": True}
        return {
            "ja": ja,
            "en": sheets.translate_history(ja),
            "rental": bool(self.extraction.rental_car_note),
            "unset": False,
        }

    @property
    def equipment(self) -> list[str]:
        return [str(item) for item in _json_list(self.extraction.equipment if self.extraction else None)]

    @property
    def lot_url(self) -> str | None:
        return f"https://banzai24.com/car/JP/{self.lot.banzai_id}" if self.lot.banzai_id else None

    # Each of these renders one cross-check inline next to the value it checked,
    # rather than collecting the four into a separate block: the point of a
    # cross-check is to qualify a number, and it qualifies it best when it is
    # printed against it.

    @property
    def mileage_note(self) -> str | None:
        """The sheet's exact mileage against the API's rounded one."""
        if self.extraction is None or self.extraction.sheet_mileage_km is None:
            return None
        if self.checks is None or self.checks.mileage is None:
            return None
        api = f"{self.lot.mileage_km:,} km" if self.lot.mileage_km is not None else "—"
        return f"API said {api}" if self.checks.mileage else f"API says {api}"

    @property
    def chassis_note(self) -> str | None:
        if self.extraction is None or not self.extraction.chassis_full:
            return None
        return f"API masked {self.lot.body_number}" if self.lot.body_number else None

    @property
    def grade_note(self) -> str | None:
        if self.checks is None or self.checks.grade is not False:
            return None
        return f"API says {self.lot.grade_origin}"


def _check_class(ok: bool | None) -> str:
    """``ok`` / ``bad`` / ``unknown`` — the CSS hook for a cross-check result."""
    return {True: "ok", False: "bad"}.get(ok, "unknown")


def _verdict_class(check) -> str:
    """The CSS hook for a requirement verdict, or ``""`` for "not asked".

    Empty rather than "unknown" when there is no check at all: a search that
    never asked about drivetrain has not failed to answer, and a "?" on a field
    nobody tested would be a question the page invented.
    """
    if check is None:
        return ""
    return {"pass": "req-pass", "fail": "req-fail"}.get(check.verdict, "req-unknown")


# --- collecting a run --------------------------------------------------------


@dataclass(frozen=True)
class Group:
    """One heading and the cards under it."""

    key: str
    label: str
    blurb: str
    views: list[LotView]

    def __len__(self) -> int:
        return len(self.views)


@dataclass
class Report:
    run_dir: Path
    views: list[LotView]
    missing: list[str] = field(default_factory=list)   # in the run, not in the DB
    cyprus_reason: str | None = None                   # why the € column is empty
    bid_reason: str | None = None                      # why *no* card has a bid price
    definition: SearchDefinition | None = None         # the search this run ran
    search_reason: str | None = None                   # why it is missing, or stale
    output: Path | None = None

    @property
    def grouped(self) -> list[Group]:
        """The three groups, in order, empty ones dropped.

        Empty is not "zero of these" — an empty *fails a requirement* heading on
        a morning where nothing failed is a heading you learn to skip, and the
        counts are in the header anyway.

        ``[]`` when the run named no search: those lots were never judged, and
        one ungrouped list is the honest rendering of that.
        """
        if self.definition is None:
            return []
        groups = []
        for key in GROUP_ORDER:
            views = [view for view in self.views if view.group == key]
            if views:
                groups.append(Group(key, GROUP_LABELS[key], GROUP_BLURBS[key], views))
        return groups

    def count(self, key: str) -> int:
        return sum(1 for view in self.views if view.group == key)

    @property
    def flagged(self) -> int:
        """Lots with a finding about the *car*, so an unread sheet does not count."""
        return sum(
            1 for view in self.views
            if any(flag.severity >= NEEDS_EYES for flag in view.flags)
        )

    @property
    def extracted(self) -> int:
        return sum(1 for view in self.views if view.extraction)

    @property
    def unread(self) -> int:
        return len(self.views) - self.extracted

    @property
    def quoted(self) -> int:
        """Cards carrying a bid block — priced *or* explained.

        Distinct from "cards showing a number": a card saying "no table row for
        2017" is still doing this feature's job. Zero is the case where a table
        is missing entirely, and the only case where the header may claim that
        nothing below has a bid price.
        """
        return sum(1 for view in self.views if view.quote)

    def summary(self) -> str:
        bits = [f"{len(self.views)} lot{'' if len(self.views) == 1 else 's'}"]
        if self.definition is not None:
            bits += [f"{len(group)} {group.label}" for group in self.grouped]
        else:
            bits.append(f"{self.extracted} with sheet data")
            if self.unread:
                bits.append(f"{self.unread} sheet(s) not read yet")
        if self.flagged:
            bits.append(f"{self.flagged} flagged")
        if self.missing:
            bits.append(f"{len(self.missing)} not in {db.DB_PATH.name}")
        if self.output:
            bits.append(f"-> {self.output}")
        return ", ".join(bits)


def collect(
    run_dir: Path,
    all_lots: bool = False,
    pricer: CyprusPricer | None = None,
    bid_pricer: BidPricer | None = None,
    definition: SearchDefinition | None = None,
) -> Report:
    """Gather one run's lots into sorted, render-ready views.

    The run directory decides *which* lots — it is the record of what this fetch
    was about — and the database decides *what is known* about them, since
    extractions and bids accumulate across runs. A lot in the run but not yet in
    the database is rendered from the run file anyway, with a note: the answer to
    "why is this lot missing" should be visible in the report, not require
    remembering that ``normalize`` was skipped.

    The **saved search** decides what "good" means, and is loaded from its file
    by the name the run recorded rather than from the run itself — so re-tuning
    a requirement and re-rendering this morning costs nothing. Runs fetched
    before saved searches were files named none, and render as one ungrouped
    list: they were never judged against anything, and inventing a verdict for
    them would be the report claiming to know something it does not.
    """
    payload = json.loads((run_dir / "lots.json").read_text(encoding="utf-8"))
    search_reason = None
    if definition is None:
        definition, search_reason = search.for_run(payload)

    rows, _problems = normalize.load_run(run_dir, all_lots=all_lots)
    numbers = [row["lot_number"] for row in rows]

    stored = db.lots_by_numbers(numbers)
    extractions = db.extractions_by_numbers(numbers)
    pricer = pricer or CyprusPricer()
    bid_pricer = bid_pricer or BidPricer()

    views, missing = [], []
    for row in rows:
        number = row["lot_number"]
        lot = stored.get(number)
        if lot is None:
            missing.append(number)
            lot = AuctionLot(**row)

        extraction = extractions.get(number)
        checks = sheets.cross_check(extraction, lot) if extraction else None

        views.append(LotView(
            lot=lot,
            extraction=extraction,
            checks=checks,
            comp=pricer.for_lot(lot),
            quote=bid_pricer.for_lot(lot, extraction),
            flags=_flags(extraction, checks),
            sheet_uri=_data_uri(_sheet_file(lot)),
            assessment=(
                judge(definition.filters, definition.requirements, lot, extraction)
                if definition else None
            ),
            requirements=definition.requirements if definition else None,
        ))

    views.sort(key=lambda view: view.sort_key)
    return Report(run_dir=run_dir, views=views, missing=missing,
                  cyprus_reason=pricer.reason, bid_reason=bid_pricer.reason,
                  definition=definition, search_reason=search_reason)


# --- rendering ---------------------------------------------------------------


def _environment() -> Environment:
    # `autoescape=True` rather than `select_autoescape`: that helper keys on the
    # file extension and would see ".j2", not ".html", and quietly leave escaping
    # off. Everything rendered here is HTML, and half of what goes into it is
    # transcribed sheet text — a stray "<" in an inspector's note would otherwise
    # eat the rest of the card.
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Japanese text goes into the page verbatim; autoescape handles the escaping,
    # these only handle the numbers.
    env.filters["yen"] = lambda v: f"¥{v:,}" if v is not None else None
    env.filters["km"] = lambda v: f"{v:,} km" if v is not None else None
    env.filters["check"] = _check_class
    env.filters["verdict"] = _verdict_class
    return env


def render(report: Report, generated_at: datetime | None = None,
           jpy_per_eur: float | None = None) -> str:
    """The whole page as one string. No file written, so this is testable."""
    template = _environment().get_template("report.html.j2")
    return template.render(
        report=report,
        views=report.views,
        run_name=report.run_dir.name,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        damage_codes=sheets.DAMAGE_CODES,
        jpy_per_eur=jpy_per_eur,
    )


def run_report(
    run_dir: Path,
    output: Path | None = None,
    all_lots: bool = False,
    jpy_per_eur: float | None = None,
    bid_prices: Path | None = None,
    area_prices: Path | None = None,
) -> Report:
    """Build ``<run>/report.html``. Overwrites — regenerating is the normal case."""
    report = collect(
        run_dir,
        all_lots=all_lots,
        bid_pricer=BidPricer(bid_prices_path=bid_prices, area_prices_path=area_prices),
    )
    output = output or run_dir / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(report, jpy_per_eur=jpy_per_eur), encoding="utf-8")
    report.output = output
    return report
