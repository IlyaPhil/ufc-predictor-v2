"""
Сопоставление имен бойцов из разных источников с id ufcstats.

Принцип: лучше оставить имя неразрешенным, чем связать бой не с тем бойцом.
Автоматически принимаются только однозначные совпадения; спорные случаи
попадают в отчет и решаются вручную через data/manual/fighter_aliases.csv
"""
import csv
import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from ufc.config import MANUAL_DIR

MANUAL_ALIASES_PATH = MANUAL_DIR / "fighter_aliases.csv"

SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

# Уменьшительные формы имен: 'Alex Hernandez' = 'Alexander Hernandez'.
# Закрытый словарь — сравниваем только явные пары, а не «похожесть» строк
NAME_FORMS = [
    {"alex", "alexander", "alexandre", "aleksandr", "aleksander"}, {"zach", "zachary", "zack"},
    {"cam", "cameron"}, {"nick", "nicholas", "nicolas", "nico"}, {"joe", "joseph", "joey"},
    {"mike", "michael"}, {"matt", "matthew"}, {"chris", "christopher"}, {"dan", "daniel", "danny"},
    {"dave", "david"}, {"jim", "james", "jimmy", "jimmie"}, {"tom", "thomas", "tommy"},
    {"tony", "anthony"}, {"rob", "robert", "bob", "bobby", "robbie"}, {"will", "william", "bill", "billy"},
    {"ben", "benjamin"}, {"sam", "samuel"}, {"andy", "andrew"}, {"steve", "steven", "stephen"},
    {"jon", "jonathan"}, {"johnny", "john"}, {"ed", "eddie", "edward"}, {"greg", "gregory"},
    {"jeff", "jeffrey"}, {"josh", "joshua"}, {"jake", "jacob"}, {"ken", "kenny", "kenneth"},
    {"rick", "ricky", "richard", "rich"}, {"ray", "raymond"}, {"charlie", "charles", "chuck"},
    {"pat", "patrick"}, {"phil", "phillip", "philip"}, {"tim", "timothy"}, {"vince", "vincent"},
    {"nate", "nathan", "nathaniel"}, {"ron", "ronald", "ronnie"}, {"don", "donald"},
    {"gabe", "gabriel"}, {"manny", "manuel"}, {"rafa", "rafael"}, {"max", "maxim", "maximilian"},
    {"sergey", "sergei"}, {"dmitry", "dmitriy", "dmitrii"}, {"yuri", "yury"},
]
_CANONICAL_FORM = {form: min(group) for group in NAME_FORMS for form in group}

# Лимиты весовых категорий, фунты
WEIGHT_LIMITS = {
    "Strawweight": 115, "Women's Strawweight": 115,
    "Flyweight": 125, "Women's Flyweight": 125,
    "Bantamweight": 135, "Women's Bantamweight": 135,
    "Featherweight": 145, "Women's Featherweight": 145,
    "Lightweight": 155, "Welterweight": 170, "Middleweight": 185,
    "Light Heavyweight": 205, "Heavyweight": 265,
}


def normalize(name: str) -> str:
    """
    'José Aldo Jr.' -> 'jose aldo': без диакритики, пунктуации и суффиксов
    """
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch)).lower()
    tokens = re.sub(r"[^a-z0-9]+", " ", name).split()
    return " ".join(t for t in tokens if t not in SUFFIXES)


def compact(name: str) -> str:
    """
    Ключ без пробелов: 'Yi Sak Lee' и 'YiSak Lee' совпадают
    """
    return normalize(name).replace(" ", "")


def name_form_key(name: str) -> str | None:
    """
    Ключ с канонической формой имени: 'Alex Hernandez' и 'Alexander Hernandez' -> 'alex hernandez'.
    None, если первое имя не из словаря
    """
    tokens = normalize(name).split()
    if len(tokens) < 2 or tokens[0] not in _CANONICAL_FORM:
        return None
    return " ".join([_CANONICAL_FORM[tokens[0]], *tokens[1:]])


def placeholder_id(name: str) -> str:
    return "unk:" + compact(name)


@dataclass
class Resolution:
    fighter_id: str | None
    method: str
    note: str = ""


class NameResolver:
    """
    Разрешает имя в id бойца по цепочке правил: ручной алиас -> точное имя ->
    нормализованное имя -> уменьшительная форма имени -> имя как подмножество токенов. На каждом шаге
    несколько кандидатов сужаются по весу, если известна весовая категория
    """
    def __init__(self, fighters: list[tuple[str, str, float | None, str | None]], manual: dict[tuple[str, str], str]):
        # fighters: (id, name, weight_lbs, dob)
        self.weights = {fid: w for fid, _, w, _ in fighters}
        self.dobs = {fid: dob for fid, _, _, dob in fighters}
        self.names = {fid: name for fid, name, _, _ in fighters}
        self.manual = manual
        self.by_exact: dict[str, list[str]] = {}
        self.by_compact: dict[str, list[str]] = {}
        self.by_name_form: dict[str, list[str]] = {}
        self.tokens: dict[str, set[str]] = {}
        for fid, name, _, _ in fighters:
            self.by_exact.setdefault(name.strip(), []).append(fid)
            self.by_compact.setdefault(compact(name), []).append(fid)
            if key := name_form_key(name):
                self.by_name_form.setdefault(key, []).append(fid)
            self.tokens[fid] = set(normalize(name).split())

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> "NameResolver":
        rows = conn.execute("SELECT id, name, weight_lbs, dob FROM fighters WHERE is_placeholder = 0").fetchall()
        return cls(rows, load_manual_aliases())

    def _by_age(self, ids: list[str], on_date: str | None) -> list[str]:
        """
        Отсекает кандидатов, которым на дату боя меньше 18 или больше 45 лет.
        Кандидаты без даты рождения остаются
        """
        if not on_date:
            return ids
        year = int(on_date[:4])
        return [i for i in ids if not self.dobs.get(i) or 18 <= year - int(self.dobs[i][:4]) <= 45]

    def _by_weight(self, ids: list[str], weight_class: str | None) -> list[str]:
        limit = WEIGHT_LIMITS.get(weight_class or "")
        if limit is None:
            return ids
        # Текущий вес в профиле может отличаться от категории боя, если боец менял дивизион,
        # поэтому берем кандидатов в пределах 15 фунтов, а из них — ближайшего, если он
        # заметно (на 10+ фунтов) ближе остальных
        dist = {i: abs(self.weights[i] - limit) for i in ids if self.weights.get(i)}
        close = sorted((d, i) for i, d in dist.items() if d <= 15)
        if len(close) > 1 and close[1][0] - close[0][0] >= 10:
            return [close[0][1]]
        return [i for _, i in close]

    def _pick(self, ids: list[str], method: str, weight_class: str | None, on_date: str | None) -> Resolution | None:
        if len(ids) == 1:
            return Resolution(ids[0], method)
        if len(ids) > 1:
            narrowed = self._by_age(ids, on_date)
            if len(narrowed) == 1:
                return Resolution(narrowed[0], f"{method}+age")
            narrowed = self._by_weight(narrowed, weight_class)
            if len(narrowed) == 1:
                return Resolution(narrowed[0], f"{method}+weight")
            return Resolution(None, "ambiguous", f"{method}: {', '.join(ids)}")
        return None

    def resolve(self, name: str, source: str, weight_class: str | None = None,
                on_date: str | None = None) -> Resolution:
        name = name.strip()
        if (source, name) in self.manual:
            return Resolution(self.manual[(source, name)], "manual")
        if ("*", name) in self.manual:
            return Resolution(self.manual[("*", name)], "manual")

        form_key = name_form_key(name)
        for ids, method in ((self.by_exact.get(name, []), "exact"),
                            (self.by_compact.get(compact(name), []), "normalized"),
                            (self.by_name_form.get(form_key, []) if form_key else [], "name_form")):
            res = self._pick(ids, method, weight_class, on_date)
            if res:
                return res

        # 'Ilimbek Akylbek' -> 'Ilimbek Akylbek Uulu': все токены одного имени входят в другое.
        # Требуем минимум 2 общих токена, иначе 'Jose Silva' совпадет с половиной ростера
        toks = set(normalize(name).split())
        if len(toks) >= 2:
            subset = [fid for fid, t in self.tokens.items() if toks <= t or (t <= toks and len(t) >= 2)]
            res = self._pick(subset, "token_subset", weight_class, on_date)
            if res and res.fighter_id:
                return res

        suggestions = process.extract(name, self.names, scorer=fuzz.WRatio, limit=3)
        note = "; ".join(f"{n} ({fid}, {score:.0f})" for n, score, fid in suggestions)
        return Resolution(None, "unresolved", note)


def load_manual_aliases() -> dict[tuple[str, str], str]:
    """
    Читает ручные алиасы. source='*' применяется к любому источнику
    """
    if not MANUAL_ALIASES_PATH.exists():
        return {}
    with MANUAL_ALIASES_PATH.open() as f:
        return {(r["source"], r["name"].strip()): r["fighter_id"].strip() for r in csv.DictReader(f)}


def store_aliases(conn: sqlite3.Connection, source: str, resolved: dict[str, Resolution]) -> None:
    conn.executemany(
        "INSERT INTO fighter_aliases (source, name, fighter_id, method, note) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (source, name) DO UPDATE SET fighter_id = excluded.fighter_id, "
        "method = excluded.method, note = excluded.note",
        [(source, name, r.fighter_id, r.method, r.note) for name, r in resolved.items()],
    )


def resolve_names(conn: sqlite3.Connection, source: str,
                  names: list[tuple[str, str | None, str | None]]) -> dict[str, Resolution]:
    """
    Разрешает имена внешнего источника (ESPN, bestfightodds) и сохраняет результат в fighter_aliases.
    names: (имя, весовая категория, дата боя) — категория и дата помогают различить тезок
    """
    resolver = NameResolver.from_db(conn)
    resolved = {name: resolver.resolve(name, source, wc, on_date) for name, wc, on_date in names}
    store_aliases(conn, source, resolved)
    return resolved
