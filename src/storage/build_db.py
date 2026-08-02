"""Build the SQLite run database from the raw synthetic sources.

Run with:  python -m src.storage.build_db --rebuild
"""

import argparse
from pathlib import Path

from ..ingestion.normalize import normalize_all
from ..ingestion.run_registry import DATA_RAW
from .db import DB_PATH, connect, init_db, table_counts, write_all


def build(db_path: Path = DB_PATH, data_raw: Path = DATA_RAW, rebuild: bool = True) -> dict[str, int]:
    data = normalize_all(data_raw)
    print(data.summary())

    with connect(db_path, create=True) as conn:  # the one path allowed to create it
        init_db(conn, rebuild=rebuild)
        write_all(conn, data.runs, data.observations, data.samples)
        counts = table_counts(conn)

    print(f"\nWrote {db_path}")
    for table, count in counts.items():
        print(f"  {table:<14} {count:>8,} rows")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="drop and recreate the tables before loading (default: append)",
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"database path (default: {DB_PATH})")
    parser.add_argument("--data-raw", type=Path, default=DATA_RAW, help="raw source directory")
    args = parser.parse_args()

    build(db_path=args.db, data_raw=args.data_raw, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
