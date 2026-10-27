"""
Проверки качества исторических данных.

Каждая проверка — SQL-запрос, возвращающий id «подозрительных» боев.
Проверки не останавливают загрузку, а дают отчет: что-то может быть
ошибкой источника, а что-то — реальной редкостью (например, бой без статистики)
"""
import sqlite3

# Форматы, где все раунды по 5 минут
FIVE_MIN_FORMATS = "('5', '5-5', '5-5-5', '5-5-5-5', '5-5-5-5-5')"

LANDED_ATTEMPTED = ("sig_str", "total_str", "td", "head", "body", "leg", "distance", "clinch", "ground")

CHECKS = {
    # Статистика по раундам
    "stats: нанесено больше, чем выброшено":
        "SELECT DISTINCT bout_id FROM bout_round_stats WHERE "
        + " OR ".join(f"{c}_l > {c}_a" for c in LANDED_ATTEMPTED),
    "stats: значимых ударов больше, чем всего ударов":
        "SELECT DISTINCT bout_id FROM bout_round_stats WHERE sig_str_l > total_str_l",
    "stats: номер раунда больше запланированного":
        "SELECT DISTINCT s.bout_id FROM bout_round_stats s JOIN bouts b ON b.id = s.bout_id "
        "WHERE b.rounds_scheduled IS NOT NULL AND s.round > b.rounds_scheduled",
    "stats: контроль дольше 5 минут в 5-минутном раунде":
        "SELECT DISTINCT s.bout_id FROM bout_round_stats s JOIN bouts b ON b.id = s.bout_id "
        f"WHERE b.round_minutes IN {FIVE_MIN_FORMATS} AND s.ctrl_sec > 300",
    "stats: число раундов не совпадает с раундом окончания":
        "SELECT s.bout_id FROM bout_round_stats s JOIN bout_results r USING (bout_id) "
        "GROUP BY s.bout_id HAVING MAX(s.round) != MAX(r.end_round)",
    "stats: статистика только по одному из бойцов":
        "SELECT bout_id FROM bout_round_stats GROUP BY bout_id, round HAVING COUNT(*) != 2",

    # Результаты
    "results: нет победителя":
        "SELECT bout_id FROM bout_results WHERE winner IS NULL",
    "results: раунд окончания больше запланированного":
        "SELECT r.bout_id FROM bout_results r JOIN bouts b ON b.id = r.bout_id "
        "WHERE b.rounds_scheduled IS NOT NULL AND r.end_round > b.rounds_scheduled",
    "results: время в раунде больше 5 минут при 5-минутных раундах":
        "SELECT r.bout_id FROM bout_results r JOIN bouts b ON b.id = r.bout_id "
        f"WHERE b.round_minutes IN {FIVE_MIN_FORMATS} AND r.end_time_sec > 300",
    "results: неизвестна общая длительность боя":
        "SELECT bout_id FROM bout_results WHERE total_time_sec IS NULL",

    # Бойцы и бои
    "bouts: один боец в обоих углах":
        "SELECT id FROM bouts WHERE red_id = blue_id",
    "bouts: боец не сопоставлен с профилем (заглушка)":
        "SELECT id FROM bouts WHERE red_id LIKE 'unk:%' OR blue_id LIKE 'unk:%'",
    "bouts: возраст бойца < 18 или > 50 на дату боя":
        "SELECT b.id FROM bouts b JOIN events e ON e.id = b.event_id "
        "JOIN fighters f ON f.id IN (b.red_id, b.blue_id) "
        "WHERE f.dob IS NOT NULL AND (julianday(e.date) - julianday(f.dob)) / 365.25 NOT BETWEEN 18 AND 50",
    "bouts: у бойца два боя в один день (вне турниров-гран-при)":
        "SELECT b.id FROM bouts b JOIN events e ON e.id = b.event_id "
        "JOIN (SELECT f_id, e2.date FROM (SELECT red_id AS f_id, event_id, is_tournament FROM bouts "
        "      UNION ALL SELECT blue_id, event_id, is_tournament FROM bouts) x "
        "      JOIN events e2 ON e2.id = x.event_id WHERE x.is_tournament = 0 "
        "      GROUP BY f_id, e2.date HAVING COUNT(*) > 1) d "
        "  ON d.date = e.date AND d.f_id IN (b.red_id, b.blue_id)",
    "bouts: нет статистики по раундам":
        "SELECT id FROM bouts WHERE id NOT IN (SELECT bout_id FROM bout_round_stats)",
}


def run_checks(conn: sqlite3.Connection) -> list[tuple[str, int, list[str]]]:
    """
    Возвращает [(проверка, число боев, до 5 примеров id)]
    """
    report = []
    for name, query in CHECKS.items():
        ids = sorted({row[0] for row in conn.execute(query)})
        report.append((name, len(ids), ids[:5]))
    return report


def summary(report: list[tuple[str, int, list[str]]]) -> str:
    return "; ".join(f"{name}={n}" for name, n, _ in report if n)
