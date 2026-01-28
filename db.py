from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "spalnik.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(schema_path: str) -> None:
    schema_file = Path(schema_path)
    sql = schema_file.read_text(encoding="utf-8")

    with _connect() as conn:
        conn.executescript(sql)
        conn.commit()


def create_booking(
    tg_user_id: Optional[int],
    tg_username: Optional[str],
    date: str,
    time: str,
    guests: int,
    name: str,
    phone: str,
    comment: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO bookings (tg_user_id, tg_username, date, time, guests, name, phone, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tg_user_id, tg_username, date, time, guests, name, phone, comment),
        )
        conn.commit()
        return int(cur.lastrowid)


