"""Join a run directory and the database into one browsable ``report.html``.

**Read-only, and self-contained.** The sheet images are inlined as data URIs and
the CSS is inline, so the file opens by double-click and keeps working after the
run directory is copied to another machine, mailed, or archived. Nothing here
writes to the database and nothing here touches the network.

This is the step a spreadsheet cannot do: *the sheet scan next to the fields
read off it*. Grade 4.5 with an `A1` on the roof means nothing without the
picture — you need to see how big the scratch is drawn.

Several sources meet here, and each answers something the others cannot:

* the **run directory** says which lots this run is about (``lots.json``), and
  carries the exchange rates the morning was priced at (``rates.json``);
* **auction.db** holds what is known about them across every run — the API
  fields and the paid extraction;
* the **bid tables** under ``inputs/`` turn that into the number you type into
  the bidding platform — see :mod:`banzai24.bidding`;
* the **model specs** under ``cars/inputs/`` turn that bid into a landed cost in
  euro — see :mod:`price_calculator`;
* and the **trims** beside them say, in English, which of a model's four cars
  the sheet's グレード box named — see :mod:`cars.trims`.

Regenerating is free — no network, no browser, no model call — so a template
tweak is a re-run of ``report``, never a re-fetch or a re-extract.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from cars import trims
from cars.definitions import Car

from . import db, glossary, normalize, search, sheets
from .bidding import BidPricer, BidQuote
from .models import AuctionLot, SheetExtraction
from .money import format_yen
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
#
# ``unstated-trim`` sits between the two. It is not a disagreement — nothing has
# contradicted anything — but it is the only badge whose answer is in the
# photographs rather than on the sheet, and it is a €1.5M-per-thousand question
# on a Harrier. Below a mismatch because a wrong car beats an unnamed trim, above
# low confidence because a smudged sheet can be re-read for pennies and a trim
# line nobody typed cannot be.
MISMATCH, UNSTATED_TRIM, LOW_CONFIDENCE_SEV = 50, 40, 30
NEEDS_EYES = LOW_CONFIDENCE_SEV

# Cross-checks that are **not** requirements, and so still earn a badge. Grade
# and mileage disagreements moved out: those two are re-judged as requirements
# now (``docs/adr/0001-sheet-outranks-api.md``), and a lot that fails one is
# already sitting in the *fails a requirement* group with the exact figure
# printed against the value it broke. A badge saying the same thing in a second
# vocabulary is how a page stops being read.
STRUCTURAL_CHECKS = ("chassis", "registration")


def _flags(view: "LotView") -> list[Flag]:
    """Every reason this lot wants your attention, most urgent first.

    Deliberately not deduplicated into one "needs review" boolean: *why* a lot
    is flagged decides what you do about it. A chassis mismatch is a different
    car; a low confidence score is a legible-sheet problem; an unstated trim is
    a question only the photographs can answer.

    Takes the whole view rather than the two rows it used to, because the trim
    badge is a question about *both* halves of a card — the listing's trim line
    and the sheet's グレード box — and reading one without the other would fire
    it on lots whose sheet already says which car this is.
    """
    extraction, checks = view.extraction, view.checks
    flags = []

    if checks and (bad := [name for name in checks.disagreements
                           if name in STRUCTURAL_CHECKS]):
        # The API and the sheet disagree about a fact both claim to know, and one
        # no requirement tests: this may not be the car the listing describes.
        # It sits at the top of whichever group the lot is in, including *meets
        # all requirements* — which is uncomfortable, and correct.
        flags.append(Flag("mismatch", f"{', '.join(bad)} mismatch", MISMATCH))

    if view.trim_unstated:
        # The search splits one car on the trim line — see
        # `cars/reference/harrier-grades.md` — and this lot names no trim on
        # either side. The exclusion kept it deliberately, so it is not a
        # failure and does not move group; it is a car whose price question is
        # open until somebody looks at the photographs.
        flags.append(Flag("unstated-trim", "trim not stated", UNSTATED_TRIM))

    if extraction and extraction.confidence is not None and extraction.confidence < LOW_CONFIDENCE:
        flags.append(Flag("low-confidence", f"confidence {extraction.confidence:.2f}",
                          LOW_CONFIDENCE_SEV))

    # A blank 車検 box is deliberately not flagged. It is a real cost — the buyer
    # pays to put the car back on the road — but it is also the common case on
    # export lots, so a badge for it fired on most of the page and crowded out
    # the findings that are actually unusual. The fact still shows in the card,
    # against the 車検 field where the price of it is read off.

    return flags


# --- landed cost and margin --------------------------------------------------


class LandedPricer:
    """What a lot costs on Cyprus plates, against what it sells for here.

    Second pricer on this page: :class:`BidPricer` says what to type into the
    platform, and this one says what winning at that price costs on Cyprus
    plates. Only the landed total reaches the card — the resale estimate and the
    margin to it are computed and not printed, because a curve fitted to asking
    prices was crowding out the condition read off the sheet. See
    :mod:`price_calculator` for the arithmetic — a port of the sheet, kept pure
    so it can be checked against the sheet's own worked example.

    **The costs come from the run, not from the clock.** ``fetch`` stamps
    ``rates.json`` *and* ``costs.json`` into the run directory the morning it
    runs; a run made before those existed, or on a morning the rate API was down,
    simply has no landed cost on its cards. Re-computing at today's rate — or
    against today's exporter fees, which went up five bands in August — would
    mean this page quietly disagreeing in September with the decision you made in
    August, which is worth less than a blank line.

    **The max bid does not.** It is read live off the search's bands, so
    re-rendering an old run prices it at the bids you hold now rather than the
    ones you held that morning. That is a deliberate exception and not an
    oversight: an exporter's fee changes *under* you, while a max bid changes
    because you changed it, and the version you want to see is yours. See
    ``docs/adr/0004-bid-prices-are-read-live.md``; the run's own provenance keeps
    the record of what it was priced at on the day.

    The **auction price is the lot's ``max_bid``**, not its ``bid_reduced``.
    ``max_bid`` is the all-in maximum *at the auction* — hammer plus the house's
    area price — and every yen of it is paid in Japan for the car, so all of it
    belongs in the customs value the VAT is charged on. Pricing from
    ``bid_reduced`` would drop the area cost out of the landed total and flatter
    every margin by ¥4,000–¥47,000.
    """

    def __init__(self, run_dir: Path | None = None, rates=None, costs=None):
        from cars.specs import ModelSpecs
        from price_calculator.sources import read_costs, read_rates

        self.rates = rates if rates is not None else (
            read_rates(run_dir) if run_dir is not None else None)
        self.costs = costs if costs is not None else (
            read_costs(run_dir) if run_dir is not None else None)
        self.reason: str | None = None
        if self.rates is None or self.costs is None:
            self.specs = None
            self.available = False
            self.reason = ("no exchange rates for this run" if self.rates is None
                           else "no cost book stamped into this run")
            return

        self.specs = ModelSpecs()
        self.available = self.specs.available
        self.reason = self.specs.reason

    def for_lot(self, lot: AuctionLot, quote: BidQuote | None,
                extraction: SheetExtraction | None = None):
        """A :class:`~price_calculator.calculator.Margin`, a reason string, or ``None``.

        ``None`` when the pricer itself is unavailable — that reason is
        report-wide and prints once in the header rather than on every card. A
        lot with no ``max_bid`` has nothing to price and also returns ``None``:
        the bid line above already says why, and repeating it is the same fact
        printed twice.
        """
        from banzai24.bidding import sheet_first
        from price_calculator.sources import margin_for

        if not self.available or quote is None or quote.max_bid is None:
            return None

        year, mileage = sheet_first(lot, extraction)
        return margin_for(
            make=lot.mark, model=lot.model, year=year, mileage_km=mileage,
            auction_price_jpy=quote.max_bid, rates=self.rates, costs=self.costs,
            # No market: the card prints the landed cost and nothing else, and
            # the estimate behind the rest costs a full `bazaraki.db` query per
            # report. See `margin_for`.
            specs=self.specs, market=None,
        )


# --- one lot, ready to render ------------------------------------------------


# The sheets arrive as JPEG and the photographs as WebP, and a data URI that
# names the wrong one renders as a blank box rather than as a wrong-looking
# image — so the type is read off the suffix fetch chose by sniffing the bytes.
_MEDIA_TYPES = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def _data_uri(path: Path | None) -> str | None:
    """An image as ``data:image/jpeg;base64,…``.

    Inlining rather than linking is what makes the report survive being copied:
    a ``<img src="sheets/47-1312-35159.jpg">`` breaks the moment the HTML is
    moved out of its run directory, and a link back to banzai24's image service
    would need the network and would rot when they rotate the token.
    """
    if path is None or not path.exists():
        return None
    media = _MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
    return f"data:{media};base64,{base64.standard_b64encode(path.read_bytes()).decode('ascii')}"


def _photo_uris(run_dir: Path, lot_number: str) -> list[str]:
    """This lot's downloaded photographs, inlined, in banzai24's own order.

    Found on disk under the run rather than through a database column, unlike
    the sheet: the photographs are worth nothing outside the report and are
    never read, hashed or paid for, so a column recording where they sit would
    be a schema to migrate for no question it can answer. A run fetched before
    photos existed — or with ``--no-photos`` — simply has none, and the card
    renders as it always did.
    """
    # Imported here rather than at module scope, for the same reason
    # normalize.attach_sheet does it: fetch pulls in playwright, and rendering a
    # saved run has no business requiring a browser to be installed.
    from .fetch import photo_files

    uris = [_data_uri(path) for path in photo_files(run_dir, lot_number)]
    return [uri for uri in uris if uri]


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
    quote: BidQuote | None = None      # None only when a bid table is missing
    margin: object | None = None       # Margin, a reason string, or None (see LandedPricer)
    flags: list[Flag] = field(default_factory=list)
    sheet_uri: str | None = None
    photo_uris: list[str] = field(default_factory=list)   # a strip under the sheet
    assessment: Assessment | None = None   # None when the run named no search
    requirements: object | None = None     # the [sheet] section, for the card
    lot_filters: object | None = None      # the [api] section, for the card
    car: Car | None = None                 # the search's car — trims are per-car

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
    def trim(self) -> dict | None:
        """``{printed, en, note, matched, blank, unasked}`` for the グレード box.

        ``None`` before a sheet has been read, because the card's title already
        carries the API's own trim line and a second empty row saying nothing
        would only push the scan further down.

        Once a sheet *has* been read there are four answers, and the row shows
        which one it is rather than collapsing them:

        * the box was **read and matched** — the Japanese, with the English
          under it and one line on how to tell that trim apart in a photograph;
        * the box was **read and not matched** — the Japanese alone, and the
          name of the file to teach. A 60系 Harrier lands here, and so does the
          first car of a model nobody has written a trims table for;
        * the box was **blank** — rare, and worth saying out loud, because on a
          Harrier a blank trim box is the one lot the whole four-file search
          arrangement cannot place;
        * the sheet was read **before the trim was asked for** — every
          extraction in the database predating this field. Distinguishable from
          a blank box only by the model's own recorded output, which is why the
          test below is on ``raw_json``: the column is null in both cases, and
          telling the operator "the box was blank" about a sheet nobody ever
          looked at that box on would be inventing a fact.

        A fifth is not an answer about the car at all: ``trims.toml`` will not
        parse. That degrades to the Japanese plus the parser's complaint rather
        than raising, on the rule the rest of this module follows — one bad
        input must not cost you the page. It is the same trade
        :class:`price_calculator.sources.ModelSpecs` makes and the opposite of
        the cost book's, and the blast radius is why: a mis-edited gloss table
        costs every card one line of English, while a mis-edited cost book makes
        every number on the page wrong.
        """
        if self.extraction is None:
            return None

        # A substring test on the raw response rather than a parse of it:
        # `raw_json` is the whole API envelope with the model's JSON nested as
        # text inside it, and the only way this name appears anywhere in it is
        # if the schema that produced it had the field.
        if "trim_ja" not in (self.extraction.raw_json or ""):
            return {"printed": None, "en": None, "note": None,
                    "matched": False, "blank": False, "unasked": True}

        try:
            reading = trims.read(self.car.key if self.car else None,
                                 self.extraction.trim_ja)
        except trims.TrimTableError as exc:
            return {"printed": self.extraction.trim_ja, "en": None,
                    "note": f"trims.toml did not load: {exc}",
                    "matched": False, "blank": False, "unasked": False}

        if reading is None:
            return {"printed": None, "en": None, "note": None,
                    "matched": False, "blank": True, "unasked": False}
        return {
            "printed": reading.printed,
            "en": reading.en,
            "note": reading.trim.note if reading.trim else None,
            "matched": reading.matched,
            "blank": False,
            "unasked": False,
        }

    # The listing's own trim line is the other half of the same question, and
    # the half the search's `[api]` grade rules are actually written against.
    # The two rows sit together on the card for the obvious reason: when they
    # disagree, seeing them a paragraph apart is how you fail to notice.

    @property
    def trim_rule(self) -> str | None:
        """The ``[api]`` grade rule this lot's trim line was judged against.

        Printed beside the line rather than once in the header, because the
        header's rule is the search's and the question on a card is always about
        the one car in front of you. ``None`` when the search names no grades,
        which is also when the row is not rendered.
        """
        bans = getattr(self.lot_filters, "exclude_model_grades", ()) or ()
        wants = getattr(self.lot_filters, "model_grades", ()) or ()
        said = []
        if wants:
            said.append(f"the search wants {'/'.join(wants)}")
        if bans:
            said.append(f"{'and' if said else 'the search'} bans {'/'.join(bans)}")
        return ", ".join(said) or None

    @property
    def trim_unstated(self) -> bool:
        """Does a search that splits on the trim line have no trim line to read?

        True only when *both* sources are silent: the listing's Модификация is
        empty and the sheet's グレード box has nothing printed in it either — an
        unread sheet included, because an unread sheet has not said no.

        A sheet that printed something counts as an answer even when
        ``trims.toml`` could not gloss it. The badge asks whether anyone has
        named this car's trim, not whether this repo can read the name; the
        Japanese is on the card either way, and a badge that stayed up next to a
        legible box would be teaching the operator to ignore it.
        """
        if self.check("trim") is None:
            return False                      # the search does not split on trim
        if (self.lot.modification or "").strip():
            return False
        trim = self.trim
        return not (trim and trim["printed"])

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

    @cached_property
    def _glossary(self) -> dict[str, str | None]:
        """The term table, read once per card.

        Per card rather than per process: a report is thirty file reads of a
        small JSON, which costs nothing measurable, and a table cached for the
        life of the process would go stale the moment `extract` and `report` ran
        in the same one.
        """
        return glossary.load()

    @property
    def equipment(self) -> list[dict]:
        """``[{ja, en}, …]`` — each item as printed, with its English beside it.

        Glossed from ``banzai24/inputs/glossary.json`` rather than translated
        here: the same ``純正ナビ`` must read the same on every card, and this
        report makes no model calls. An item nobody has glossed yet prints its
        Japanese alone — see :mod:`banzai24.glossary`.
        """
        items = [str(item) for item in
                 _json_list(self.extraction.equipment if self.extraction else None)]
        return glossary.gloss(items, self._glossary)

    @property
    def warnings(self) -> list[dict]:
        """The 注意事項欄 box, item by item, each with its English.

        The box is one string on the sheet and one string in the database, but
        it is *written* as a list — ``取保　スペアキー　後送`` is three separate
        things wrong or missing — and a buyer prices them one at a time. Split
        and glossed the same way equipment is, for the same reason: the terms
        repeat across sheets, so they are worth translating once.
        """
        if self.extraction is None:
            return []
        return glossary.gloss(glossary.split_warnings(self.extraction.warnings_ja),
                              self._glossary)

    @property
    def warnings_note(self) -> str | None:
        """The whole-box translation, shown only when no item could be glossed.

        Rows extracted before the glossary existed carry a ``warnings_en``
        sentence from the sheet read. It is worth keeping on the page while the
        glossary has nothing to say about that box — but once the items are
        glossed it is the same information twice, and the per-item lines are the
        ones the operator asked for.
        """
        if self.extraction is None or not self.extraction.warnings_en:
            return None
        if any(item["en"] for item in self.warnings):
            return None
        return self.extraction.warnings_en

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
    bid_reason: str | None = None                      # why *no* card has a bid price
    landed_reason: str | None = None                   # why *no* card has a landed cost
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
    bid_pricer: BidPricer | None = None,
    landed_pricer: LandedPricer | None = None,
    definition: SearchDefinition | None = None,
    area_prices: Path | None = None,
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
    # The bands are the search's, so the pricer is built after the search is
    # resolved: a run that named none gets no bid column and says so once, in the
    # header, rather than being priced off whichever car's table loaded first.
    bid_pricer = bid_pricer or BidPricer(
        bands=definition.bands if definition else (),
        car=definition.spec.car if definition and definition.spec else None,
        area_prices_path=area_prices,
    )
    landed_pricer = landed_pricer or LandedPricer(run_dir)

    views, missing = [], []
    for row in rows:
        number = row["lot_number"]
        lot = stored.get(number)
        if lot is None:
            missing.append(number)
            lot = AuctionLot(**row)

        extraction = extractions.get(number)
        checks = sheets.cross_check(extraction, lot) if extraction else None

        quote = bid_pricer.for_lot(lot, extraction)
        view = LotView(
            lot=lot,
            extraction=extraction,
            checks=checks,
            quote=quote,
            margin=landed_pricer.for_lot(lot, quote, extraction),
            sheet_uri=_data_uri(_sheet_file(lot)),
            photo_uris=_photo_uris(run_dir, number),
            assessment=(
                judge(definition.filters, definition.lot_filters,
                      definition.requirements, lot, extraction)
                if definition else None
            ),
            requirements=definition.requirements if definition else None,
            lot_filters=definition.lot_filters if definition else None,
            car=definition.spec.car if definition and definition.spec else None,
        )
        # After the view rather than into its constructor: one flag is a question
        # about the assessment and the trim reading together, and both of those
        # live on the view.
        view.flags = _flags(view)
        views.append(view)

    views.sort(key=lambda view: view.sort_key)
    return Report(run_dir=run_dir, views=views, missing=missing,
                  bid_reason=bid_pricer.reason,
                  landed_reason=landed_pricer.reason,
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
    env.filters["yen"] = format_yen
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
    area_prices: Path | None = None,
) -> Report:
    """Build ``<run>/report.html``. Overwrites — regenerating is the normal case.

    The max bids are not a parameter any more: they belong to the search this run
    named, which ``collect`` resolves from the run itself. Only the area prices
    stay overridable, because that file is shared by every search and is the one
    a test wants to point somewhere else.
    """
    report = collect(run_dir, all_lots=all_lots, area_prices=area_prices)
    output = output or run_dir / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(report, jpy_per_eur=jpy_per_eur), encoding="utf-8")
    report.output = output
    return report
