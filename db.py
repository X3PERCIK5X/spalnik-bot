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
        # add missing columns for existing DBs
        cols = {row[1] for row in conn.execute("PRAGMA table_info(bookings)").fetchall()}
        if "reminder_sent" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0")
        if "reminder_sent_at" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN reminder_sent_at TEXT")
        if "canceled" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN canceled INTEGER NOT NULL DEFAULT 0")
        if "canceled_at" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN canceled_at TEXT")
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


def list_pending_bookings() -> list[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT * FROM bookings
            WHERE canceled = 0 AND reminder_sent = 0
            ORDER BY id DESC
            """
        )
        return cur.fetchall()


def mark_reminder_sent(booking_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE bookings SET reminder_sent = 1, reminder_sent_at = datetime('now') WHERE id = ?",
            (booking_id,),
        )
        conn.commit()


def mark_canceled(booking_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE bookings SET canceled = 1, canceled_at = datetime('now') WHERE id = ?",
            (booking_id,),
        )
        conn.commit()


def get_booking(booking_id: int) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
        return cur.fetchone()
