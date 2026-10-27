"""
Подключение к SQLite и журналирование запусков
"""
import sqlite3
import traceback
from contextlib import contextmanager
from pathlib import Path

from ufc.config import DB_PATH
from ufc.http import utc_now

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


@contextmanager
def sync_run(conn: sqlite3.Connection, task: str):
    """
    Пишет запуск задачи в sync_runs. Внутри блока можно дополнить run['details']
    """
    cur = conn.execute("INSERT INTO sync_runs (task, started_at) VALUES (?, ?)", (task, utc_now()))
    conn.commit()
    run = {"id": cur.lastrowid, "details": ""}
    try:
        yield run
    except Exception:
        conn.rollback()
        conn.execute("UPDATE sync_runs SET finished_at = ?, status = 'error', details = ? WHERE id = ?",
                     (utc_now(), traceback.format_exc()[-2000:], run["id"]))
        conn.commit()
        raise
    conn.execute("UPDATE sync_runs SET finished_at = ?, status = 'ok', details = ? WHERE id = ?",
                 (utc_now(), run["details"], run["id"]))
    conn.commit()
