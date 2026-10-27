"""
Снимки коэффициентов на победу (moneyline) с bestfightodds.com.

Сайт показывает текущую линию по нескольким букмекерам; историю движения линии
мы получаем сами, регулярно снимая ее (затем снимки попадают в odds_snapshots)
"""
import json
import re

from bs4 import BeautifulSoup

from ufc.http import PoliteSession, save_raw, utc_now

BASE_URL = "https://www.bestfightodds.com"


def list_ufc_events(html: str) -> list[tuple[str, str]]:
    """
    Главная страница -> [(slug, название)] турниров UFC
    """
    soup = BeautifulSoup(html, "lxml")
    events = {}
    for header in soup.select("div.table-header"):
        link = header.select_one('a[href^="/events/"]')
        name = re.sub(r"\s+Odds\b.*$", "", header.get_text(" ", strip=True))
        if link and name.upper().startswith("UFC"):
            events[link["href"].removeprefix("/events/")] = name
    return list(events.items())


def parse_american(text: str) -> int | None:
    m = re.search(r"[+-]\d+", text)
    return int(m[0]) if m else None


def parse_event(html: str) -> list[dict]:
    """
    Страница турнира -> строки (matchup_id, fighter_a, fighter_b, bookmaker, odds_a, odds_b).
    Ячейка коэффициента содержит data-li='[id букмекера, сторона 1|2, id боя]'.
    Букмекеры без коэффициента на бой в результат не попадают
    """
    soup = BeautifulSoup(html, "lxml")
    table = next((t for t in soup.select("table.odds-table") if t.select_one("td[data-li]")), None)
    if table is None:
        return []
    # Имя букмекера — в ссылке заголовка, а если ссылки нет — в первой строке текста ячейки
    bookmakers = {th["data-b"]: (th.a or th).get_text("\n", strip=True).split("\n")[0]
                  for th in table.select("thead th[data-b]")}

    matchups: dict[str, dict] = {}
    m = None
    for tr in table.select("tbody tr"):
        if "pr" in (tr.get("class") or []):  # строки пропов (тоталы, способ победы) пока не собираем
            continue
        th = tr.find("th")
        if th is None:
            continue
        # id боя стоит только у первого бойца пары; строка второго бойца идет следом без него
        if th.has_attr("data-admin-matchup"):
            m = matchups.setdefault(th["data-admin-matchup"], {"names": [], "odds": {}})
        if m is None:
            continue
        m["names"].append(th.get_text(strip=True))
        for td in tr.select("td[data-li]"):
            # Формат moneyline — [букмекер, сторона, бой]; 2 элемента бывают у пустых ячеек
            li = json.loads(td["data-li"])
            if len(li) != 3:
                continue
            book_id, side, _ = li
            m["odds"].setdefault(str(book_id), [None, None])[side - 1] = parse_american(td.get_text())

    rows = []
    for matchup_id, m in matchups.items():
        if len(m["names"]) != 2:
            continue
        for book_id, (odds_a, odds_b) in m["odds"].items():
            rows.append({
                "matchup_id": matchup_id,
                "fighter_a": m["names"][0], "fighter_b": m["names"][1],
                "bookmaker": bookmakers.get(book_id, f"book:{book_id}"),
                "odds_a": odds_a, "odds_b": odds_b,
            })
    return rows


def collect(session: PoliteSession) -> tuple[str, list[dict]]:
    """
    Снимок коэффициентов всех предстоящих турниров UFC -> (captured_at, строки)
    """
    captured_at = utc_now()
    front = session.get(BASE_URL + "/")
    save_raw("bestfightodds", "index.html", front.content, captured_at)

    rows = []
    for slug, name in list_ufc_events(front.text):
        resp = session.get(f"{BASE_URL}/events/{slug}")
        save_raw("bestfightodds", f"{slug}.html", resp.content, captured_at)
        for r in parse_event(resp.text):
            rows.append({"captured_at": captured_at, "source": "bestfightodds",
                         "event_slug": slug, "event_name": name, **r})
    return captured_at, rows
