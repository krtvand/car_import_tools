"""Back up the project's SQLite databases into backups/.

Uses SQLite's online backup API rather than a file copy, so a backup taken
while a scrape is mid-write still lands as a consistent database (and picks up
anything sitting in the -wal file).

Examples:
    uv run python backup_dbs.py
    uv run python backup_dbs.py --keep 5
    uv run python backup_dbs.py --out-dir /Volumes/usb/bazaraki-backups
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

# Both databases live at the repo root so reports can join across them.
DATABASES = [ROOT / "bazaraki.db", ROOT / "auction.db"]
BACKUP_DIR = ROOT / "backups"


def backup_one(src: Path, out_dir: Path) -> Path:
    """Copy ``src`` to a timestamped file in ``out_dir`` and verify the result."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"{src.stem}_{stamp}{src.suffix}"

    # Connect read-only: a backup run must never create or modify the source.
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError(f"integrity check failed for {dest}")
        finally:
            target.close()
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    return dest


def prune(db_name: str, out_dir: Path, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest backups of ``db_name``; return removed."""
    existing = sorted(out_dir.glob(f"{db_name}_*.db"))  # timestamp sorts as date
    removed = existing[:-keep] if keep else []
    for path in removed:
        path.unlink()
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=BACKUP_DIR,
        help=f"Where to write backups (default: {BACKUP_DIR})",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=10,
        help="Keep this many backups per database; 0 disables pruning (default: 10)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        action="append",
        dest="dbs",
        help="Back up this database instead of the defaults (repeatable)",
    )
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for src in args.dbs or DATABASES:
        if not src.exists():
            print(f"Skipped {src.name} (not created yet)")
            continue

        dest = backup_one(src, out_dir)
        size_kb = dest.stat().st_size / 1024
        print(f"Backed up {src.name} -> {dest} ({size_kb:,.0f} KB)")

        for old in prune(src.stem, out_dir, args.keep):
            print(f"  pruned {old.name}")


if __name__ == "__main__":
    main()
