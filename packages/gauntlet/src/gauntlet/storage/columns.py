"""Bringing a table written by an earlier version up to its current columns."""

from __future__ import annotations

import sqlite3


def add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Add each named column the table lacks, with its declared type.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing table as it was, so a
    database written before a column existed would otherwise never gain it.
    """
    present = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    conn.commit()
