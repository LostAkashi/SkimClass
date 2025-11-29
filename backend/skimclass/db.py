
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "skimclass.db"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                course_name TEXT,
                mode TEXT,
                created_at TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                ts TEXT,
                image_path TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                idx INTEGER,
                title TEXT,
                summary TEXT,
                open_questions TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                ts TEXT,
                content TEXT
            );
            """
        )

        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def create_session(session_id: str, course_name: str, mode: str) -> str:
    created_at = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, course_name, mode, created_at) VALUES (?, ?, ?, ?)",
            (session_id, course_name, mode, created_at),
        )
        conn.commit()
    return created_at


def add_capture(session_id: str, ts: str, image_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO captures (session_id, ts, image_path) VALUES (?, ?, ?)",
            (session_id, ts, image_path),
        )
        conn.commit()


def get_captures(session_id: str):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, session_id, ts, image_path FROM captures WHERE session_id = ? ORDER BY ts ASC",
            (session_id,),
        )
        return cur.fetchall()


def clear_segments(session_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
        conn.commit()


def add_segment(session_id: str, idx: int, title: str, summary: str, open_questions_json: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO segments (session_id, idx, title, summary, open_questions)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, idx, title, summary, open_questions_json),
        )
        conn.commit()


def get_segments(session_id: str):
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, session_id, idx, title, summary, open_questions
            FROM segments
            WHERE session_id = ?
            ORDER BY idx ASC
            """,
            (session_id,),
        )
        return cur.fetchall()


def save_quiz(session_id: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO quizzes (session_id, ts, content) VALUES (?, ?, ?)",
            (session_id, datetime.utcnow().isoformat(), content),
        )
        conn.commit()
