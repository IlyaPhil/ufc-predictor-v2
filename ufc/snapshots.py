"""
Файловое хранилище снимков источников (кард ESPN, коэффициенты).

Сбор и загрузка разделены: сборщик только дописывает JSONL-файлы и может работать
где угодно (например, в GitHub Actions), а загрузка в БД выполняется локально
в любой момент и идемпотентна — повторная загрузка тех же файлов ничего не дублирует
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ufc.config import SNAPSHOT_DIR
from ufc.names import resolve_names

# источник -> таблица в БД
TABLES = {"espn": "card_snapshots", "bestfightodds": "odds_snapshots"}


def write_snapshot(source: str, captured_at: str, rows: list[dict], root: Path = SNAPSHOT_DIR) -> Path:
    ts = datetime.fromisoformat(captured_at)
    path = root / source / ts.strftime("%Y/%m") / f"{ts.strftime('%Y-%m-%dT%H%M%SZ')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_rows(source: str, root: Path = SNAPSHOT_DIR) -> list[dict]:
    rows = []
    for path in sorted((root / source).rglob("*.jsonl")):
        with path.open() as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows


def load(conn: sqlite3.Connection, root: Path = SNAPSHOT_DIR) -> str:
    parts = []
    for source, table in TABLES.items():
        rows = read_rows(source, root)
        if not rows:
            parts.append(f"{source}: 0 files")
            continue
        cols = list(rows[0])
        with conn:
            before = conn.total_changes
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [tuple(r[c] for c in cols) for r in rows])
            inserted = conn.total_changes - before
            # Имена разрешаем заново каждый раз: могли появиться новые профили или ручные алиасы
            if source == "espn":
                names = {(r[k], r["weight_class"], r["event_date"][:10]) for r in rows for k in ("fighter_a", "fighter_b")}
            else:
                names = {(r[k], None, None) for r in rows for k in ("fighter_a", "fighter_b")}
            resolved = resolve_names(conn, source, sorted(names, key=lambda x: (x[0], x[2] or "")))
        unresolved = sum(1 for r in resolved.values() if not r.fighter_id)
        parts.append(f"{source}: rows={len(rows)} new={inserted} names_unresolved={unresolved}")
    return "; ".join(parts)
