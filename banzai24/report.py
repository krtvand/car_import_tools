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
  fields, the paid extraction, the bid ledger;
* **bazaraki.db** holds the Cyprus asking prices, which is the only thing that
  turns a grade and a mileage into a decision about money.

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

from . import db, normalize, sheets
from .models import AuctionLot, BidRecord, SheetExtraction
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


# Severities, named because they are read twice: once to sort, once to decide
# what counts as "flagged" in the header. ``NOT_READ`` sits below
# :data:`NEEDS_EYES` on purpose — an unextracted sheet is a gap in the report,
# not a finding about the car, and counting it as one would mean a freshly
# fetched run reports every lot as flagged and the count stops meaning anything.
MISMATCH, BID, LOW_CONFIDENCE_SEV, NO_SHAKEN, NOT_READ = 50, 40, 30, 20, 10
NEEDS_EYES = NO_SHAKEN


def _flags(
    lot: AuctionLot,
    extraction: SheetExtraction | None,
    checks: CrossCheck | None,
    bids: list[BidRecord],
) -> list[Flag]:
    """Every reason this lot wants your attention, most urgent first.

    Deliberately not deduplicated into one "needs review" boolean: *why* a lot
    is flagged decides what you do about it. A chassis mismatch is a different
    car; a low confidence score is a legible-sheet problem; no shaken is a real
    cost to add to the bid.
    """
    flags = []

    if checks and (bad := checks.disagreements):
        # The API and the sheet disagree about a fact both claim to know. Either
        # the model misread it or the lot is not the lot the listing describes —
        # and the chassis case means the second.
        flags.append(Flag("mismatch", f"{', '.join(bad)} mismatch", MISMATCH))

    if bids:
        # You have already committed money here. It stays at the top of the page
        # for as long as the run exists.
        flags.append(Flag("bid", f"{len(bids)} bid{'s' if len(bids) > 1 else ''} placed", BID))

    if extraction and extraction.confidence is not None and extraction.confidence < LOW_CONFIDENCE:
        flags.append(Flag("low-confidence", f"confidence {extraction.confidence:.2f}",
                          LOW_CONFIDENCE_SEV))

    if extraction and not extraction.shaken_expiry_raw:
        # A blank 車検 box is a fact, not a gap: no valid shaken means the buyer
        # pays to put the car back on the road, which belongs in the bid.
        flags.append(Flag("no-shaken", "no shaken", NO_SHAKEN))

    if extraction is None:
        # Not a problem with the lot — a gap in the report. Flagged low so it
        # sorts under the real findings but is never silently absent.
        flags.append(Flag("not-read", f"sheet {lot.sheet_status}", NOT_READ))

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
    bids: list[BidRecord] = field(default_factory=list)
    checks: CrossCheck | None = None
    comp: CyprusComp | None = None
    flags: list[Flag] = field(default_factory=list)
    sheet_uri: str | None = None

    @property
    def sort_key(self) -> tuple:
        """Flagged lots first, then in the order they cross the block.

        Within a severity band the trade time is the tiebreak, because that is
        the order you will actually have to make decisions in.
        """
        severity = max((flag.severity for flag in self.flags), default=0)
        return (
            -severity,
            str(self.lot.trade_date or ""),
            self.lot.trade_time or "",
            self.lot.lot_number,
        )

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
    def damage_marks(self) -> list[dict]:
        """``[{panel, code, meaning}]`` — the legend joined in at render time.

        ``meaning`` is ``None`` for a code the legend does not cover. That
        happens for real: one extraction returned ``トビA`` (a stone chip),
        which the prompt correctly passed through verbatim rather than forcing
        into a known letter.
        """
        marks = []
        for mark in _json_list(self.extraction.damage_marks if self.extraction else None):
            if not isinstance(mark, dict):
                continue
            code = str(mark.get("code") or "")
            marks.append({
                "panel": mark.get("panel") or "",
                "code": code,
                "meaning": sheets.DAMAGE_CODES.get(code[:1]),
            })
        return marks

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


# --- collecting a run --------------------------------------------------------


@dataclass
class Report:
    run_dir: Path
    views: list[LotView]
    missing: list[str] = field(default_factory=list)   # in the run, not in the DB
    cyprus_reason: str | None = None                   # why the € column is empty
    output: Path | None = None

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

    def summary(self) -> str:
        bits = [f"{len(self.views)} lot{'' if len(self.views) == 1 else 's'}"]
        if self.flagged:
            bits.append(f"{self.flagged} flagged")
        bits.append(f"{self.extracted} with sheet data")
        if self.unread:
            bits.append(f"{self.unread} sheet(s) not read yet")
        if self.missing:
            bits.append(f"{len(self.missing)} not in {db.DB_PATH.name}")
        if self.output:
            bits.append(f"-> {self.output}")
        return ", ".join(bits)


def collect(
    run_dir: Path,
    all_lots: bool = False,
    pricer: CyprusPricer | None = None,
) -> Report:
    """Gather one run's lots into sorted, render-ready views.

    The run directory decides *which* lots — it is the record of what this fetch
    was about — and the database decides *what is known* about them, since
    extractions and bids accumulate across runs. A lot in the run but not yet in
    the database is rendered from the run file anyway, with a note: the answer to
    "why is this lot missing" should be visible in the report, not require
    remembering that ``normalize`` was skipped.
    """
    rows, _problems = normalize.load_run(run_dir, all_lots=all_lots)
    numbers = [row["lot_number"] for row in rows]

    stored = db.lots_by_numbers(numbers)
    extractions = db.extractions_by_numbers(numbers)
    bids = db.bids_by_numbers(numbers)
    pricer = pricer or CyprusPricer()

    views, missing = [], []
    for row in rows:
        number = row["lot_number"]
        lot = stored.get(number)
        if lot is None:
            missing.append(number)
            lot = AuctionLot(**row)

        extraction = extractions.get(number)
        checks = sheets.cross_check(extraction, lot) if extraction else None
        lot_bids = bids.get(number, [])

        views.append(LotView(
            lot=lot,
            extraction=extraction,
            bids=lot_bids,
            checks=checks,
            comp=pricer.for_lot(lot),
            flags=_flags(lot, extraction, checks, lot_bids),
            sheet_uri=_data_uri(_sheet_file(lot)),
        ))

    views.sort(key=lambda view: view.sort_key)
    return Report(run_dir=run_dir, views=views, missing=missing,
                  cyprus_reason=pricer.reason)


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
) -> Report:
    """Build ``<run>/report.html``. Overwrites — regenerating is the normal case."""
    report = collect(run_dir, all_lots=all_lots)
    output = output or run_dir / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(report, jpy_per_eur=jpy_per_eur), encoding="utf-8")
    report.output = output
    return report
