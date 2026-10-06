from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from auth import hash_password


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "heksa_inventory.db"
DEFAULT_LABS = (
    "Lab Listrik",
    "Lab Tekanan",
    "Lab Suhu",
    "Lab Massa",
    "Lab Dimensi",
    "Lab Kimia",
    "Lab Akustik",
    "Lab Torsi",
)


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS labs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lab_name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_no TEXT NOT NULL UNIQUE,
                equipment_name TEXT NOT NULL,
                certificate_no TEXT,
                specification TEXT,
                lab_id INTEGER,
                calibration_date TEXT,
                expiry_date TEXT,
                barcode TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lab_id) REFERENCES labs (id),
                FOREIGN KEY (created_by) REFERENCES users (id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                equipment_id INTEGER,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (equipment_id) REFERENCES equipment (id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_no ON equipment (equipment_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_barcode ON equipment (barcode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_certificate_no ON equipment (certificate_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_status ON equipment (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipment_lab_id ON equipment (lab_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_equipment_id ON activity_logs (equipment_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_user_id ON activity_logs (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions (token_hash)")

        admin = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            ("admin",),
        ).fetchone()

        if admin is None:
            conn.execute(
                """
                INSERT INTO users (name, username, password_hash, role, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "Admin",
                    "admin",
                    hash_password("Heksa123!"),
                    "administrator",
                    "active",
                ),
            )

        lab_count = conn.execute("SELECT COUNT(*) AS total FROM labs").fetchone()["total"]
        if lab_count == 0:
            conn.executemany(
                "INSERT INTO labs (lab_name, status) VALUES (?, ?)",
                [(lab_name, "active") for lab_name in DEFAULT_LABS],
            )
