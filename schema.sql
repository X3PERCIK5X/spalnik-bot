PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  tg_user_id INTEGER,
  tg_username TEXT,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  guests INTEGER NOT NULL,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  comment TEXT
);
