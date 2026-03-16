import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional


class Database:
    def __init__(self):
        self.db_dir = Path.home() / ".simple_assistant"
        self.db_dir.mkdir(exist_ok=True)
        self.db_path = str(self.db_dir / "assistant.db")
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    original_text   TEXT,
                    processed_text  TEXT,
                    context_text    TEXT
                );

                CREATE TABLE IF NOT EXISTS hot_words (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    word        TEXT UNIQUE NOT NULL,
                    created_at  TEXT NOT NULL
                );
            """)
            # Migration: add context_text column for older DBs
            try:
                conn.execute("ALTER TABLE history ADD COLUMN context_text TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

    # ── History ───────────────────────────────────────────────────────────────

    def add_history(
        self,
        action: str,
        original: str,
        processed: str,
        context: str = "",
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO history (created_at, action, original_text, processed_text, context_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(), action, original, processed, context),
            )

    def get_history(self, limit: int = 300, offset: int = 0) -> List[Dict]:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT id, created_at, action, original_text, processed_text, context_text "
                "FROM history ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def clear_history(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM history")

    # ── Hot words ─────────────────────────────────────────────────────────────

    def add_hot_word(self, word: str):
        word = word.strip()
        if not word:
            return
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO hot_words (word, created_at) VALUES (?, ?)",
                    (word, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError:
                pass

    def remove_hot_word(self, word: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM hot_words WHERE word = ?", (word,))

    def get_hot_words(self) -> List[str]:
        with self._conn() as conn:
            cursor = conn.execute("SELECT word FROM hot_words ORDER BY word")
            return [row[0] for row in cursor.fetchall()]
