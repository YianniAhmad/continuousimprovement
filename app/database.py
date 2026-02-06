import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Any

DATABASE_URL = os.getenv("DATABASE_URL")  # Render should set this to a full postgres://... URL
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "continuousco.db")


def _is_postgres() -> bool:
    """
    Render DATABASE_URL should look like:
      postgres://user:pass@host:5432/dbname
      postgresql://user:pass@host:5432/dbname
    """
    if not DATABASE_URL:
        return False
    return DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


def placeholder() -> str:
    """SQL placeholder for the active DB."""
    return "%s" if _is_postgres() else "?"


def returning_id_clause() -> str:
    """Postgres supports RETURNING id; SQLite doesn't need it."""
    return " RETURNING id" if _is_postgres() else ""


def get_inserted_id(cur: Any) -> int:
    """
    After an INSERT:
      - Postgres: uses RETURNING id and fetchone()
      - SQLite: uses cursor.lastrowid
    Works with psycopg dict_row (row["id"]) and tuple rows (row[0]).
    """
    if _is_postgres():
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Expected a row from Postgres RETURNING id, got None.")

        # psycopg dict_row behaves like a dict: row["id"]
        try:
            return int(row["id"])  # type: ignore[index]
        except Exception:
            pass

        # fallback: tuple-like row (id,)
        try:
            return int(row[0])
        except Exception as e:
            raise RuntimeError(f"Could not read inserted id from row={row!r}") from e

    return int(cur.lastrowid)


@contextmanager
def get_conn() -> Iterator[Any]:
    """
    Returns a DB connection.
    - Postgres on Render if DATABASE_URL is set
    - SQLite locally otherwise
    """
    if _is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()


def init_db() -> None:
    if _is_postgres():
        _init_postgres()
    else:
        _init_sqlite()


def _init_sqlite() -> None:
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            position INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            answer_text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS ai_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL,
            summary_text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)

        conn.commit()


def _init_postgres() -> None:
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS forms (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT now()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id SERIAL PRIMARY KEY,
            form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
            question_text TEXT NOT NULL,
            position INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id SERIAL PRIMARY KEY,
            form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            answer_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS ai_summaries (
            id SERIAL PRIMARY KEY,
            form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
            summary_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """)

        conn.commit()
