"""The runs index: one page listing recent runs, and the only tab ``--open`` opens.

A morning is several runs — a two-car day is two — and ``report --open`` used to
open one browser tab per report it wrote. Two cars meant two tabs, and every run
older than the one just built was reachable only through Finder. This replaces
that with a single page, always opened in the same place, carrying the recent
history as context.

Everything on it is derived from the run directory itself: the name carries the
timestamp and the car, ``lots.csv`` carries how many lots were kept, and whether
``report.html`` exists says whether the run was ever reported. So building the
index touches no network, no model and no database — which is why it is simply
rewritten in full on every ``report`` rather than kept up to date incrementally.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .fetch import RUNS_DIR

TEMPLATE_DIR = Path(__file__).parent / "templates"

# How many runs the index shows. Ten is about a fortnight of two-car mornings:
# far enough back to find the run you half-remember, short of the scroll that
# would push today's off the top of the window.
DEFAULT_LIMIT = 10

# The directory names fetch writes: `YYYY-MM-DD_HHMMSS_MAKE-MODEL`.
_STAMP_FORMAT = "%Y-%m-%d_%H%M%S"


@dataclass(frozen=True)
class RunEntry:
    """One row on the index."""

    directory: Path
    started_at: datetime | None
    car: str
    lots: int | None
    report: Path | None

    @property
    def name(self) -> str:
        return self.directory.name

    @property
    def when(self) -> str:
        """Falls back to the raw directory name, which is still readable, rather
        than showing a blank cell for a directory someone renamed by hand."""
        if self.started_at is None:
            return self.name
        return self.started_at.strftime("%a %-d %b %Y, %H:%M")

    @property
    def href(self) -> str | None:
        """Relative, so the index keeps working if ``runs/`` is moved or copied."""
        if self.report is None:
            return None
        return f"{self.directory.name}/{self.report.name}"


def _parse_name(name: str) -> tuple[datetime | None, str]:
    """Split a run directory name into when it ran and what it was looking for.

    The car segment is ``MAKE-MODEL`` and the model itself contains hyphens
    (``MAZDA-CX-30``), so it is split once from the left — splitting on every
    hyphen would render "MAZDA CX 30".
    """
    date_part, _, rest = name.partition("_")
    time_part, _, car = rest.partition("_")
    try:
        stamp = datetime.strptime(f"{date_part}_{time_part}", _STAMP_FORMAT)
    except ValueError:
        return None, car or name
    make, _, model = car.partition("-")
    return stamp, f"{make} {model}".strip() or name


def _lot_count(run_dir: Path) -> int | None:
    """Rows in ``lots.csv``, which is one per kept lot, or None if it has none.

    Parsed rather than line-counted: the flattened rows carry Japanese free text
    and a quoted field can hold a newline, so counting lines would over-count.
    """
    path = run_dir / "lots.csv"
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = sum(1 for _ in csv.reader(handle))
    except OSError:
        return None
    return max(rows - 1, 0)  # less the header


def _entry(run_dir: Path) -> RunEntry:
    started_at, car = _parse_name(run_dir.name)
    report = run_dir / "report.html"
    return RunEntry(
        directory=run_dir,
        started_at=started_at,
        car=car,
        lots=_lot_count(run_dir),
        report=report if report.exists() else None,
    )


def _run_dirs(root: Path) -> list[Path]:
    """Every run directory, newest first.

    ``lots.json`` is what makes a directory a run — the same test ``normalize``
    uses — so a stray folder under ``runs/`` is not listed.

    Ordered by the timestamp in the *name*, never by mtime. Reading a sheet with
    ``extract`` or re-rendering with ``report`` touches an old run directory, so
    an mtime sort would float last week's runs to the top of the index for it.
    """
    if not root.exists():
        return []
    return sorted(
        (d for d in root.glob("*") if (d / "lots.json").exists()),
        key=lambda d: d.name,
        reverse=True,
    )


def recent(root: Path | None = None, limit: int = DEFAULT_LIMIT) -> list[RunEntry]:
    """The newest ``limit`` runs, newest first."""
    return [_entry(d) for d in _run_dirs(root or RUNS_DIR)[:limit]]


def render(
    entries: list[RunEntry],
    generated_at: datetime | None = None,
    total: int | None = None,
) -> str:
    """The whole page as one string. No file written, so this is testable."""
    # Same autoescape reasoning as report.py: the loader keys on ".j2", so
    # `select_autoescape` would see no ".html" and quietly leave escaping off.
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("index.html.j2").render(
        entries=entries,
        total=len(entries) if total is None else total,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )


def write(
    root: Path | None = None,
    limit: int = DEFAULT_LIMIT,
    output: Path | None = None,
) -> Path:
    """(Re)write ``runs/index.html`` and return where it went.

    Always a full rewrite. The page is derived from directory names and costs
    nothing to rebuild, so the only real failure mode is staleness, and
    rebuilding it on every ``report`` removes that failure mode entirely.
    """
    root = root or RUNS_DIR
    dirs = _run_dirs(root)
    entries = [_entry(d) for d in dirs[:limit]]
    output = output or root / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(entries, total=len(dirs)), encoding="utf-8")
    return output
