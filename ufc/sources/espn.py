"""
Карды предстоящих турниров из публичного JSON API ESPN.

Каждый запуск дает полный снимок карда (затем он попадает в card_snapshots), поэтому
по истории снимков видно, когда бой появился, сменился соперник или бой отменили
"""
from datetime import datetime, timedelta, timezone

from ufc.http import PoliteSession, save_raw, utc_now

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"


def normalize_weight_class(espn_wc: str | None) -> str | None:
    """
    'W Flyweight' -> "Women's Flyweight" (как в ufcstats)
    """
    if not espn_wc:
        return None
    return "Women's " + espn_wc[2:] if espn_wc.startswith("W ") else espn_wc


def parse_scoreboard(data: dict, captured_at: str) -> list[dict]:
    rows = []
    for ev in data.get("events", []):
        comps = ev.get("competitions", [])
        # ESPN отдает бои в хронологическом порядке: главный бой последним
        for i, c in enumerate(comps):
            fighters = sorted(c["competitors"], key=lambda x: x.get("order", 0))
            if len(fighters) != 2:
                continue
            records = [next((r["summary"] for r in f.get("records", []) if r.get("type") == "total"), None)
                       for f in fighters]
            rows.append({
                "captured_at": captured_at,
                "espn_event_id": ev["id"],
                "event_name": ev["name"],
                "event_date": ev["date"],
                "espn_bout_id": c["id"],
                "card_position": len(comps) - 1 - i,
                "fighter_a": fighters[0]["athlete"]["displayName"],
                "fighter_b": fighters[1]["athlete"]["displayName"],
                "record_a": records[0],
                "record_b": records[1],
                "weight_class": normalize_weight_class(c.get("type", {}).get("abbreviation")),
                "rounds": c.get("format", {}).get("regulation", {}).get("periods"),
                "status": c.get("status", {}).get("type", {}).get("name"),
            })
    return rows


def collect(session: PoliteSession, days_ahead: int = 60) -> tuple[str, list[dict]]:
    """
    Снимок кардов всех турниров на ближайшие days_ahead дней -> (captured_at, строки)
    """
    captured_at = utc_now()
    today = datetime.now(timezone.utc).date()
    dates = f"{today:%Y%m%d}-{today + timedelta(days=days_ahead):%Y%m%d}"
    resp = session.get(SCOREBOARD_URL, params={"dates": dates})
    save_raw("espn", "scoreboard.json", resp.content, captured_at)
    return captured_at, parse_scoreboard(resp.json(), captured_at)
