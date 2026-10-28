"""
Связка боев из внешних источников (ESPN, bestfightodds) с боями ufcstats.

Бой ищется по неупорядоченной паре id бойцов (порядок бойцов в источниках — не углы)
и окну дат: у каждого источника свое представление о дате боя. Связь принимается,
только если кандидат ровно один. Результат — bout_links и производная от нее
линия закрытия bout_closing_odds; обе таблицы пересчитываются целиком
"""
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class ExternalBout:
    source: str
    external_id: str
    name_a: str
    name_b: str
    date_from: date     # окно, в котором должна лежать дата турнира ufcstats
    date_to: date


def parse_ts(ts: str) -> datetime:
    """
    '2026-10-31T21:00Z' и '2026-10-27T19:33:06+00:00' -> datetime с часовым поясом
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def espn_bouts(conn: sqlite3.Connection) -> list[ExternalBout]:
    """
    Бои из снимков ESPN; имена — из последнего снимка, окно — дата турнира ±1 день
    (ESPN дает время по UTC, ufcstats — местную дату турнира)
    """
    rows = conn.execute("""
        SELECT espn_bout_id, fighter_a, fighter_b, event_date FROM card_snapshots s
        WHERE captured_at = (SELECT MAX(captured_at) FROM card_snapshots WHERE espn_bout_id = s.espn_bout_id)
    """).fetchall()
    out = []
    for bout_id, a, b, event_date in rows:
        d = parse_ts(event_date).date()
        out.append(ExternalBout("espn", bout_id, a, b, d - timedelta(days=1), d + timedelta(days=1)))
    return out


def bestfightodds_bouts(conn: sqlite3.Connection) -> list[ExternalBout]:
    """
    Бои из снимков bestfightodds. Даты турнира в снимках нет, поэтому окно —
    от первого снимка (минус день) до последнего снимка плюс 14 дней
    """
    rows = conn.execute("""
        SELECT matchup_id, fighter_a, fighter_b, MIN(captured_at), MAX(captured_at)
        FROM odds_snapshots GROUP BY matchup_id
    """).fetchall()
    return [ExternalBout("bestfightodds", m, a, b,
                         parse_ts(first).date() - timedelta(days=1), parse_ts(last).date() + timedelta(days=14))
            for m, a, b, first, last in rows]


def link(conn: sqlite3.Connection, external: list[ExternalBout]) -> tuple[list[tuple], Counter]:
    """
    -> ([(source, external_id, bout_id, a_is_red)], счетчик исходов)
    """
    aliases = {(s, n): fid for s, n, fid in
               conn.execute("SELECT source, name, fighter_id FROM fighter_aliases WHERE fighter_id IS NOT NULL")}
    by_pair: dict[frozenset, list[tuple[date, str, str]]] = {}
    for bout_id, red_id, blue_id, d in conn.execute(
            "SELECT b.id, b.red_id, b.blue_id, e.date FROM bouts b JOIN events e ON e.id = b.event_id"):
        by_pair.setdefault(frozenset((red_id, blue_id)), []).append((date.fromisoformat(d), bout_id, red_id))

    links, stats = [], Counter()
    for ext in external:
        id_a, id_b = aliases.get((ext.source, ext.name_a)), aliases.get((ext.source, ext.name_b))
        if not id_a or not id_b:
            stats["name_unresolved"] += 1
            continue
        candidates = [c for c in by_pair.get(frozenset((id_a, id_b)), []) if ext.date_from <= c[0] <= ext.date_to]
        if not candidates:
            stats["no_bout"] += 1      # бой еще не прошел или был отменен
        elif len(candidates) > 1:
            stats["ambiguous"] += 1
        else:
            _, bout_id, red_id = candidates[0]
            links.append((ext.source, ext.external_id, bout_id, int(id_a == red_id)))
            stats["linked"] += 1
    return links, stats


def closing_odds(conn: sqlite3.Connection) -> list[tuple]:
    """
    Последний снимок каждого букмекера строго до начала турнира, в разрезе красный/синий
    """
    # Начало турнира: время начала карда из ESPN, иначе полночь UTC даты турнира ufcstats
    starts = {bout_id: parse_ts(ts) for bout_id, ts in conn.execute("""
        SELECT l.bout_id, MIN(s.event_date) FROM bout_links l
        JOIN card_snapshots s ON s.espn_bout_id = l.external_id
        WHERE l.source = 'espn' GROUP BY l.bout_id""")}
    for bout_id, d in conn.execute("SELECT b.id, e.date FROM bouts b JOIN events e ON e.id = b.event_id "
                                   "WHERE b.id IN (SELECT bout_id FROM bout_links WHERE source = 'bestfightodds')"):
        starts.setdefault(bout_id, parse_ts(d + "T00:00:00+00:00"))

    latest: dict[tuple[str, str], tuple] = {}
    for bout_id, a_is_red, bookmaker, captured_at, odds_a, odds_b in conn.execute("""
            SELECT l.bout_id, l.a_is_red, o.bookmaker, o.captured_at, o.odds_a, o.odds_b
            FROM odds_snapshots o JOIN bout_links l
              ON l.source = 'bestfightodds' AND l.external_id = o.matchup_id"""):
        if parse_ts(captured_at) >= starts[bout_id]:
            continue
        key = (bout_id, bookmaker)
        if key not in latest or captured_at > latest[key][2]:
            red, blue = (odds_a, odds_b) if a_is_red else (odds_b, odds_a)
            latest[key] = (bout_id, bookmaker, captured_at, red, blue)
    return list(latest.values())


def rebuild(conn: sqlite3.Connection) -> str:
    parts = []
    with conn:
        conn.execute("DELETE FROM bout_links")
        for source, fn in (("espn", espn_bouts), ("bestfightodds", bestfightodds_bouts)):
            links, stats = link(conn, fn(conn))
            conn.executemany("INSERT INTO bout_links VALUES (?, ?, ?, ?)", links)
            parts.append(f"{source}: " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        conn.execute("DELETE FROM bout_closing_odds")
        odds = closing_odds(conn)
        conn.executemany("INSERT INTO bout_closing_odds VALUES (?, ?, ?, ?, ?)", odds)
    parts.append(f"closing_odds_rows={len(odds)}")
    return "; ".join(parts)
