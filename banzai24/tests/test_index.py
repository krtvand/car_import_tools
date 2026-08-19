"""The runs index — ordering, parsing, and the states a run can be in."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from banzai24 import index


def make_run(root: Path, name: str, lots: int = 0, reported: bool = True) -> Path:
    """A run directory with just enough in it to be recognised as one."""
    run = root / name
    (run / "sheets").mkdir(parents=True)
    (run / "lots.json").write_text("[]", encoding="utf-8")
    rows = ["lot_number,mark,model"] + [f"{i},MAZDA,CX-30" for i in range(lots)]
    (run / "lots.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if reported:
        (run / "report.html").write_text("<!doctype html>", encoding="utf-8")
    return run


def test_parses_the_timestamp_and_the_car_out_of_the_directory_name():
    stamp, car = index._parse_name("2026-08-17_222903_TOYOTA-RAV4")
    assert stamp == datetime(2026, 8, 17, 22, 29, 3)
    assert car == "TOYOTA RAV4"


def test_keeps_hyphens_inside_the_model():
    """`MAZDA-CX-30` is make + model, not three words."""
    _, car = index._parse_name("2026-08-16_105939_MAZDA-CX-30")
    assert car == "MAZDA CX-30"


def test_an_unparseable_name_still_yields_an_entry(tmp_path):
    make_run(tmp_path, "hand-renamed-run")
    entry = index.recent(tmp_path)[0]
    assert entry.started_at is None
    assert entry.when == "hand-renamed-run"  # falls back, never blank


def test_newest_first(tmp_path):
    for name in ("2026-08-16_105939_MAZDA-CX-30",
                 "2026-08-17_222903_TOYOTA-RAV4",
                 "2026-08-17_182056_MAZDA-CX-5"):
        make_run(tmp_path, name)
    assert [e.name for e in index.recent(tmp_path)] == [
        "2026-08-17_222903_TOYOTA-RAV4",
        "2026-08-17_182056_MAZDA-CX-5",
        "2026-08-16_105939_MAZDA-CX-30",
    ]


def test_ordering_ignores_mtime(tmp_path):
    """Re-reading an old run's sheets must not float it to the top.

    `extract` and `report` both write into an existing run directory, so an
    mtime sort would reorder the index every time you worked on an old run.
    """
    old = make_run(tmp_path, "2026-08-01_090000_MAZDA-CX-30")
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    (old / "report.html").write_text("<!doctype html>rebuilt", encoding="utf-8")
    (old / "extractions.jsonl").write_text("{}\n", encoding="utf-8")

    assert index.recent(tmp_path)[0].name == "2026-08-17_222903_TOYOTA-RAV4"


def test_limit_takes_the_newest(tmp_path):
    for day in range(1, 13):
        make_run(tmp_path, f"2026-08-{day:02d}_090000_MAZDA-CX-30")
    entries = index.recent(tmp_path, limit=10)
    assert len(entries) == 10
    assert entries[0].name.startswith("2026-08-12")
    assert entries[-1].name.startswith("2026-08-03")


def test_a_directory_without_lots_json_is_not_a_run(tmp_path):
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    (tmp_path / "scratch").mkdir()
    (tmp_path / "index.html").write_text("previous build", encoding="utf-8")
    assert len(index.recent(tmp_path)) == 1


def test_lot_count_excludes_the_header(tmp_path):
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4", lots=3)
    assert index.recent(tmp_path)[0].lots == 3


def test_lot_count_survives_a_newline_inside_a_quoted_field(tmp_path):
    """Sheet text is Japanese free text; counting lines would over-count."""
    run = make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    (run / "lots.csv").write_text(
        'lot_number,note\n1,"first\nsecond"\n2,plain\n', encoding="utf-8"
    )
    assert index.recent(tmp_path)[0].lots == 2


def test_an_unreported_run_is_listed_with_no_link(tmp_path):
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4", reported=False)
    entry = index.recent(tmp_path)[0]
    assert entry.report is None
    assert entry.href is None


def test_links_are_relative(tmp_path):
    """So the index keeps working if `runs/` is copied somewhere else."""
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    assert index.recent(tmp_path)[0].href == (
        "2026-08-17_222903_TOYOTA-RAV4/report.html"
    )


def test_no_runs_at_all(tmp_path):
    assert index.recent(tmp_path) == []
    assert index.recent(tmp_path / "nonexistent") == []


# --- rendering -------------------------------------------------------------

def test_render_lists_every_entry(tmp_path):
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4", lots=2)
    make_run(tmp_path, "2026-08-16_105939_MAZDA-CX-30", lots=1)
    html = index.render(index.recent(tmp_path))
    assert "TOYOTA RAV4" in html
    assert "MAZDA CX-30" in html
    assert "2 lots" in html
    assert "1 lot" in html  # singular


def test_render_flags_an_unreported_run_with_the_command_to_fix_it(tmp_path):
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4", reported=False)
    html = index.render(index.recent(tmp_path))
    assert "unreported" in html
    assert "report runs/2026-08-17_222903_TOYOTA-RAV4" in html


def test_render_says_when_it_is_showing_a_subset(tmp_path):
    for day in range(1, 13):
        make_run(tmp_path, f"2026-08-{day:02d}_090000_MAZDA-CX-30")
    html = index.render(index.recent(tmp_path, limit=10), total=12)
    assert "10 most recent of 12" in html


def test_render_with_no_runs_says_what_to_do(tmp_path):
    html = index.render([])
    assert "No runs yet" in html
    assert "fetch" in html


def test_render_escapes_directory_names(tmp_path):
    """Autoescape is on for a reason; .j2 defeats select_autoescape."""
    make_run(tmp_path, "2026-08-17_222903_<script>")
    html = index.render(index.recent(tmp_path))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_is_self_contained(tmp_path):
    """No network at open time — the report guarantees the same."""
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    html = index.render(index.recent(tmp_path))
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html


# --- writing ---------------------------------------------------------------

def test_write_lands_next_to_the_runs(tmp_path):
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    written = index.write(tmp_path)
    assert written == tmp_path / "index.html"
    assert "TOYOTA RAV4" in written.read_text(encoding="utf-8")


def test_write_replaces_rather_than_appends(tmp_path):
    make_run(tmp_path, "2026-08-16_105939_MAZDA-CX-30")
    index.write(tmp_path)
    make_run(tmp_path, "2026-08-17_222903_TOYOTA-RAV4")
    html = index.write(tmp_path).read_text(encoding="utf-8")
    assert html.count("<!doctype html>") == 1
    assert "TOYOTA RAV4" in html and "MAZDA CX-30" in html


def test_write_counts_every_run_but_lists_only_the_limit(tmp_path):
    for day in range(1, 13):
        make_run(tmp_path, f"2026-08-{day:02d}_090000_MAZDA-CX-30")
    html = index.write(tmp_path).read_text(encoding="utf-8")
    assert "10 most recent of 12" in html
    assert "2026-08-01_090000" not in html


def test_write_with_no_runs_still_writes_a_page(tmp_path):
    written = index.write(tmp_path)
    assert written.exists()
    assert "No runs yet" in written.read_text(encoding="utf-8")
