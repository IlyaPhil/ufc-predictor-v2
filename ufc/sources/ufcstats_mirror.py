"""
История боев из ufcstats.com через CSV-зеркало (github.com/Greco1899/scrape_ufc_stats).

Зеркало каждый раз отдает полную историю, поэтому таблицы events/bouts/bout_round_stats
перезаливаются целиком, а fighters обновляются upsert-ом (на них ссылаются алиасы)
"""
import io
import re
import sqlite3
from datetime import datetime

import pandas as pd

from ufc.config import HISTORY_START, UFCSTATS_MIRROR_URL
from ufc.http import PoliteSession, save_raw, utc_now
from ufc.names import NameResolver, Resolution, load_manual_aliases, placeholder_id, store_aliases

FILES = {
    "events": "ufc_event_details.csv",
    "fight_details": "ufc_fight_details.csv",
    "results": "ufc_fight_results.csv",
    "stats": "ufc_fight_stats.csv",
    "fighter_details": "ufc_fighter_details.csv",
    "tott": "ufc_fighter_tott.csv",
}

# Порядок важен: более длинные названия проверяются раньше ('Light Heavyweight' до 'Heavyweight')
WEIGHT_CLASSES = [
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight", "Women's Featherweight",
    "Light Heavyweight", "Super Heavyweight", "Heavyweight", "Middleweight", "Welterweight",
    "Lightweight", "Featherweight", "Bantamweight", "Flyweight", "Catch Weight", "Open Weight",
]

# Столбцы вида '24 of 47' -> (landed, attempted)
OF_COLUMNS = {
    "SIG.STR.": "sig_str", "TOTAL STR.": "total_str", "TD": "td", "HEAD": "head", "BODY": "body",
    "LEG": "leg", "DISTANCE": "distance", "CLINCH": "clinch", "GROUND": "ground",
}


def id_from_url(url: str) -> str:
    return url.strip().rstrip("/").rsplit("/", 1)[-1]


def parse_weight_class(raw: str) -> tuple[str | None, bool, bool]:
    """
    'UFC Interim Lightweight Title Bout' -> ('Lightweight', is_title=True, is_tournament=False)
    """
    raw = raw.strip()
    wc = next((w for w in WEIGHT_CLASSES if w in raw), None)
    is_tournament = "Tournament" in raw
    is_title = "Title" in raw or "Championship" in raw
    return wc, is_title, is_tournament


def parse_round_minutes(time_format) -> list[int]:
    """
    '3 Rnd (5-5-5)' -> [5, 5, 5]; '1 Rnd + OT (12-3)' -> [12, 3]; 'No Time Limit' -> []
    """
    m = re.search(r"\(([\d-]+)\)", str(time_format))
    return [int(x) for x in m[1].split("-")] if m else []


def total_time_sec(round_minutes: list[int], end_round: int | None, end_time_sec: int | None) -> int | None:
    if not end_round or end_time_sec is None or len(round_minutes) < end_round:
        return None
    return sum(round_minutes[:end_round - 1]) * 60 + end_time_sec


def method_group(method: str | None) -> str | None:
    if not method:
        return None
    if method.startswith("Decision"):
        return "DEC"
    if method in ("KO/TKO", "TKO - Doctor's Stoppage"):
        return "KO/TKO"
    if method == "Submission":
        return "SUB"
    if method == "DQ":
        return "DQ"
    return "OTHER"  # Overturned, Could Not Continue, Other


def parse_height_cm(s) -> float | None:
    m = re.match(r"(\d+)' (\d+)\"", str(s))
    return round((int(m[1]) * 12 + int(m[2])) * 2.54, 1) if m else None


def parse_reach_cm(s) -> float | None:
    m = re.match(r"(\d+(?:\.\d+)?)\"", str(s))
    return round(float(m[1]) * 2.54, 1) if m else None


def parse_weight_lbs(s) -> float | None:
    m = re.match(r"(\d+(?:\.\d+)?) lbs", str(s))
    return float(m[1]) if m else None


def parse_date(s, fmt: str) -> str | None:
    try:
        return datetime.strptime(str(s).strip(), fmt).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_mmss(s) -> int | None:
    m = re.match(r"(\d+):(\d+)", str(s).strip())
    return int(m[1]) * 60 + int(m[2]) if m else None


def parse_of(s) -> tuple[int | None, int | None]:
    m = re.match(r"(\d+) of (\d+)", str(s).strip())
    return (int(m[1]), int(m[2])) if m else (None, None)


def parse_int(s) -> int | None:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def none_if_nan(v):
    return None if pd.isna(v) else v


def download(session: PoliteSession) -> dict[str, pd.DataFrame]:
    captured_at = utc_now()
    frames = {}
    for key, filename in FILES.items():
        content = session.get(f"{UFCSTATS_MIRROR_URL}/{filename}").content
        save_raw("ufcstats_mirror", filename, content, captured_at)
        frames[key] = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, na_values=[""])
    return frames


def build_fighters(tott: pd.DataFrame, details: pd.DataFrame) -> pd.DataFrame:
    tott = tott.assign(id=tott.URL.map(id_from_url))
    details = details.assign(id=details.URL.map(id_from_url))[["id", "NICKNAME"]]
    df = tott.merge(details, on="id", how="left").drop_duplicates("id")
    return pd.DataFrame({
        "id": df.id,
        "name": df.FIGHTER.str.strip(),
        "nickname": df.NICKNAME,
        "height_cm": df.HEIGHT.map(parse_height_cm),
        "weight_lbs": df.WEIGHT.map(parse_weight_lbs),
        "reach_cm": df.REACH.map(parse_reach_cm),
        "stance": df.STANCE.where(df.STANCE.notna() & (df.STANCE != "--")),
        "dob": df.DOB.map(lambda s: parse_date(s, "%b %d, %Y")),
    })


def build_bouts(results: pd.DataFrame, events: pd.DataFrame, resolver: NameResolver
                ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Resolution]]:
    event_ids = dict(zip(events.name, events.id))
    event_dates = dict(zip(events.id, events.date))
    # Бои турниров, которых нет в списке событий, отбрасываем: это турниры до HISTORY_START,
    # копии боев под старым названием турнира (Noche UFC в зеркале продублированы как
    # 'UFC Fight Night: ...') или турниры без даты, бесполезные для признаков
    results = results[results.EVENT.str.strip().isin(event_ids)].drop_duplicates("URL")

    bouts, outcomes, resolved = [], [], {}
    for r in results.rename(columns={"TIME FORMAT": "TIME_FORMAT"}).itertuples(index=False):
        event_id = event_ids[r.EVENT.strip()]
        red_name, blue_name = (n.strip() for n in r.BOUT.split(" vs. ", 1))
        wc, is_title, is_tournament = parse_weight_class(r.WEIGHTCLASS)
        ids = []
        for name in (red_name, blue_name):
            res = resolver.resolve(name, "ufcstats_bout", wc, event_dates[event_id])
            # У тезок разрешение зависит от боя, поэтому в отчет пишем первый неудачный случай
            if name not in resolved or (resolved[name].fighter_id and not res.fighter_id):
                resolved[name] = res
            ids.append(res.fighter_id or placeholder_id(name))
        bout_id = id_from_url(r.URL)
        # Число периодов берем из скобок: в '1 Rnd + OT (12-3)' их два — раунд и овертайм
        rounds = re.match(r"(\d+) Rnd", str(r.TIME_FORMAT))
        round_minutes = parse_round_minutes(r.TIME_FORMAT)
        bouts.append({
            "id": bout_id,
            "event_id": event_id,
            "red_id": ids[0], "blue_id": ids[1],
            "red_name": red_name, "blue_name": blue_name,
            "weight_class": wc, "weight_class_raw": r.WEIGHTCLASS.strip(),
            "is_title": int(is_title), "is_tournament": int(is_tournament),
            "rounds_scheduled": len(round_minutes) or (int(rounds[1]) if rounds else None),
            "round_minutes": "-".join(map(str, round_minutes)) or None,
        })
        method = none_if_nan(r.METHOD) and r.METHOD.strip()
        end_round, end_time = parse_int(r.ROUND), parse_mmss(r.TIME)
        outcomes.append({
            "bout_id": bout_id,
            "winner": {"W/L": "red", "L/W": "blue", "D/D": "draw", "NC/NC": "nc"}.get(r.OUTCOME.strip()),
            "method_group": method_group(method),
            "method": method,
            "method_details": none_if_nan(r.DETAILS) and r.DETAILS.strip(),
            "end_round": end_round,
            "end_time_sec": end_time,
            "total_time_sec": total_time_sec(round_minutes, end_round, end_time),
            "referee": none_if_nan(r.REFEREE),
        })
    return pd.DataFrame(bouts), pd.DataFrame(outcomes), resolved


def build_round_stats(stats: pd.DataFrame, bouts: pd.DataFrame, results: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Статистика привязывается к бою по паре (EVENT, BOUT), к углу — по имени из BOUT
    """
    key = results.assign(EVENT=results.EVENT.str.strip(), BOUT=results.BOUT.str.strip(),
                         bout_id=results.URL.map(id_from_url))
    key = key.drop_duplicates(["EVENT", "BOUT"], keep=False)[["EVENT", "BOUT", "bout_id"]]
    df = stats.dropna(subset=["FIGHTER", "ROUND"]).assign(EVENT=stats.EVENT.str.strip(), BOUT=stats.BOUT.str.strip())
    df = df.merge(key, on=["EVENT", "BOUT"]).merge(
        bouts[["id", "red_id", "blue_id", "red_name", "blue_name"]], left_on="bout_id", right_on="id")
    fighter = df.FIGHTER.str.strip()
    df["corner"] = None
    df.loc[fighter == df.red_name, "corner"] = "red"
    df.loc[fighter == df.blue_name, "corner"] = "blue"
    skipped = len(stats) - df.corner.notna().sum()
    df = df[df.corner.notna()]

    out = pd.DataFrame({
        "bout_id": df.bout_id,
        "fighter_id": df.red_id.where(df.corner == "red", df.blue_id),
        "corner": df.corner,
        "round": df.ROUND.str.extract(r"(\d+)", expand=False).astype(int),
        "kd": df.KD.map(parse_int),
        "sub_att": df["SUB.ATT"].map(parse_int),
        "rev": df["REV."].map(parse_int),
        "ctrl_sec": df.CTRL.map(parse_mmss),
    })
    for col, prefix in OF_COLUMNS.items():
        parsed = df[col].map(parse_of)
        out[f"{prefix}_l"] = parsed.map(lambda x: x[0])
        out[f"{prefix}_a"] = parsed.map(lambda x: x[1])
    out = out.drop_duplicates(["bout_id", "corner", "round"])
    return out, int(skipped)


def sync(conn: sqlite3.Connection, session: PoliteSession) -> str:
    frames = download(session)

    events = frames["events"]
    events = pd.DataFrame({
        "id": events.URL.map(id_from_url),
        "name": events.EVENT.str.strip(),
        "date": events.DATE.map(lambda s: parse_date(s, "%B %d, %Y")),
        "location": events.LOCATION,
    })
    events = events[events.date >= HISTORY_START]
    fighters = build_fighters(frames["tott"], frames["fighter_details"])
    resolver = NameResolver(list(fighters[["id", "name", "weight_lbs", "dob"]].itertuples(index=False, name=None)),
                            load_manual_aliases())
    bouts, outcomes, resolved = build_bouts(frames["results"], events, resolver)

    # Бойцы из боев, которых не удалось сопоставить с профилем, получают id-заглушку,
    # чтобы их бои оставались связанными между собой
    placeholders = sorted({n for n, r in resolved.items() if r.fighter_id is None})
    placeholder_rows = pd.DataFrame({"id": [placeholder_id(n) for n in placeholders], "name": placeholders})
    placeholder_rows = placeholder_rows.drop_duplicates("id")

    stats, skipped_stats = build_round_stats(frames["stats"], bouts, frames["results"])

    with conn:
        conn.execute("DELETE FROM bout_round_stats")
        conn.execute("DELETE FROM bout_results")
        conn.execute("DELETE FROM bouts")
        conn.execute("DELETE FROM events")
        fighter_cols = list(fighters.columns)
        conn.executemany(
            f"INSERT INTO fighters ({', '.join(fighter_cols)}, is_placeholder) "
            f"VALUES ({', '.join('?' * len(fighter_cols))}, 0) ON CONFLICT (id) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in fighter_cols[1:]),
            fighters.astype(object).where(fighters.notna(), None).itertuples(index=False, name=None))
        conn.executemany(
            "INSERT INTO fighters (id, name, is_placeholder) VALUES (?, ?, 1) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name",
            placeholder_rows.itertuples(index=False, name=None))
        events.to_sql("events", conn, if_exists="append", index=False)
        bouts.to_sql("bouts", conn, if_exists="append", index=False)
        outcomes.to_sql("bout_results", conn, if_exists="append", index=False)
        stats.to_sql("bout_round_stats", conn, if_exists="append", index=False)
        conn.execute("DELETE FROM fighter_aliases WHERE source = 'ufcstats_bout'")
        # В алиасы пишем только нетривиальные случаи, иначе таблица дублирует fighters
        store_aliases(conn, "ufcstats_bout", {n: r for n, r in resolved.items() if r.method != "exact"})

    return (f"events={len(events)} bouts={len(bouts)} skipped_bouts={len(frames['results']) - len(bouts)} "
            f"fighters={len(fighters)} "
            f"placeholders={len(placeholder_rows)} round_stats={len(stats)} skipped_stat_rows={skipped_stats}")
