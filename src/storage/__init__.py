"""SQLite run database.

    from src.storage import connect, read_observations
    with connect() as conn:
        obs = read_observations(conn, run_ids=["R1"])
"""

from .db import (
    DB_PATH,
    DatabaseNotBuiltError,
    connect,
    init_db,
    read_observations,
    read_runs,
    read_samples,
    require_schema,
    table_counts,
    write_all,
)

__all__ = [
    "DB_PATH",
    "DatabaseNotBuiltError",
    "connect",
    "init_db",
    "read_observations",
    "read_runs",
    "read_samples",
    "require_schema",
    "table_counts",
    "write_all",
]
