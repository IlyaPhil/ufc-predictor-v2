-- Схема БД проекта.
-- Принцип: храним атомарные факты (бои, раунды, снимки коэффициентов),
-- признаки для моделей вычисляются из них на дату боя, а не хранятся готовыми.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- История (источник: ufcstats.com через зеркало). Таблицы событий и боев
-- перезаливаются при каждой синхронизации — зеркало является источником истины.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fighters (
    id          TEXT PRIMARY KEY,   -- id ufcstats (hex из URL) или 'unk:<имя>' для неразрешенных
    name        TEXT NOT NULL,
    nickname    TEXT,
    height_cm   REAL,
    weight_lbs  REAL,
    reach_cm    REAL,
    stance      TEXT,
    dob         TEXT,               -- YYYY-MM-DD
    is_placeholder INTEGER NOT NULL DEFAULT 0  -- 1 = боец из боя, не найденный в профилях
);

CREATE TABLE IF NOT EXISTS events (
    id        TEXT PRIMARY KEY,     -- id ufcstats
    name      TEXT NOT NULL,
    date      TEXT NOT NULL,        -- YYYY-MM-DD
    location  TEXT
);

-- Только то, что известно ДО боя. Код признаков читает эту таблицу,
-- а результаты берет лишь из прошлых боев — так утечке неоткуда взяться
CREATE TABLE IF NOT EXISTS bouts (
    id               TEXT PRIMARY KEY,  -- id ufcstats
    event_id         TEXT NOT NULL REFERENCES events(id),
    red_id           TEXT NOT NULL REFERENCES fighters(id),
    blue_id          TEXT NOT NULL REFERENCES fighters(id),
    red_name         TEXT NOT NULL,     -- имя как указано в бою (может отличаться от профиля)
    blue_name        TEXT NOT NULL,
    weight_class     TEXT,              -- нормализованная категория ('Lightweight', "Women's Flyweight", ...)
    weight_class_raw TEXT,
    is_title         INTEGER NOT NULL,
    is_tournament    INTEGER NOT NULL,
    rounds_scheduled INTEGER,           -- NULL для ранних боев без лимита
    round_minutes    TEXT               -- длительности раундов, напр. '5-5-5'; NULL если неизвестны
);
CREATE INDEX IF NOT EXISTS ix_bouts_event ON bouts(event_id);
CREATE INDEX IF NOT EXISTS ix_bouts_red ON bouts(red_id);
CREATE INDEX IF NOT EXISTS ix_bouts_blue ON bouts(blue_id);

-- Целевые переменные: становятся известны только ПОСЛЕ боя
CREATE TABLE IF NOT EXISTS bout_results (
    bout_id          TEXT PRIMARY KEY REFERENCES bouts(id),
    winner           TEXT CHECK (winner IN ('red', 'blue', 'draw', 'nc')),
    method_group     TEXT CHECK (method_group IN ('KO/TKO', 'SUB', 'DEC', 'DQ', 'OTHER')),
    method           TEXT,              -- как в источнике: 'Decision - Split', "TKO - Doctor's Stoppage", ...
    method_details   TEXT,
    end_round        INTEGER,
    end_time_sec     INTEGER,           -- время внутри последнего раунда
    total_time_sec   INTEGER,           -- общая длительность боя; NULL если неизвестен формат раундов
    referee          TEXT
);

-- Статистика бойца в раунде. *_l = landed (нанесено), *_a = attempted (выброшено)
CREATE TABLE IF NOT EXISTS bout_round_stats (
    bout_id      TEXT NOT NULL REFERENCES bouts(id),
    fighter_id   TEXT NOT NULL REFERENCES fighters(id),
    corner       TEXT NOT NULL CHECK (corner IN ('red', 'blue')),
    round        INTEGER NOT NULL,
    kd           INTEGER,
    sig_str_l    INTEGER, sig_str_a    INTEGER,
    total_str_l  INTEGER, total_str_a  INTEGER,
    td_l         INTEGER, td_a         INTEGER,
    sub_att      INTEGER,
    rev          INTEGER,
    ctrl_sec     INTEGER,
    head_l       INTEGER, head_a       INTEGER,
    body_l       INTEGER, body_a       INTEGER,
    leg_l        INTEGER, leg_a        INTEGER,
    distance_l   INTEGER, distance_a   INTEGER,
    clinch_l     INTEGER, clinch_a     INTEGER,
    ground_l     INTEGER, ground_a     INTEGER,
    PRIMARY KEY (bout_id, corner, round)
);

-- ---------------------------------------------------------------------------
-- Предстоящие турниры (источник: ESPN). Только дописываем — так сохраняется
-- история изменений карда (замены, отмены).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS card_snapshots (
    captured_at     TEXT NOT NULL,      -- UTC ISO
    espn_event_id   TEXT NOT NULL,
    event_name      TEXT NOT NULL,
    event_date      TEXT NOT NULL,      -- UTC ISO начала турнира
    espn_bout_id    TEXT NOT NULL,
    card_position   INTEGER NOT NULL,   -- 0 = главный бой
    fighter_a       TEXT NOT NULL,      -- порядок ESPN (не обязательно красный угол)
    fighter_b       TEXT NOT NULL,
    record_a        TEXT,               -- полный профессиональный рекорд W-L-D, важен для дебютантов
    record_b        TEXT,
    weight_class    TEXT,
    rounds          INTEGER,
    status          TEXT,
    PRIMARY KEY (captured_at, espn_bout_id)
);

-- Последнее известное состояние карда — удобная «витрина» поверх снимков
CREATE VIEW IF NOT EXISTS upcoming_bouts AS
SELECT s.*
FROM card_snapshots s
JOIN (SELECT espn_event_id, MAX(captured_at) AS captured_at
      FROM card_snapshots GROUP BY espn_event_id) last
  USING (espn_event_id, captured_at);

-- ---------------------------------------------------------------------------
-- Коэффициенты (источник: bestfightodds). Только дописываем.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS odds_snapshots (
    captured_at   TEXT NOT NULL,
    source        TEXT NOT NULL,        -- 'bestfightodds'
    event_slug    TEXT NOT NULL,
    event_name    TEXT NOT NULL,
    matchup_id    TEXT NOT NULL,        -- id боя на стороне источника
    fighter_a     TEXT NOT NULL,
    fighter_b     TEXT NOT NULL,
    bookmaker     TEXT NOT NULL,
    odds_a        INTEGER,              -- американский формат (-245 / +233)
    odds_b        INTEGER,
    PRIMARY KEY (captured_at, source, matchup_id, bookmaker)
);

-- ---------------------------------------------------------------------------
-- Связь снимков с боями ufcstats. Производные таблицы: пересчитываются целиком
-- при каждом daily, поэтому без внешних ключей (bouts тоже перезаливается)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bout_links (
    source       TEXT NOT NULL,         -- 'espn' / 'bestfightodds'
    external_id  TEXT NOT NULL,         -- espn_bout_id / matchup_id
    bout_id      TEXT NOT NULL,         -- bouts.id
    a_is_red     INTEGER NOT NULL,      -- 1, если fighter_a источника — красный угол
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS ix_bout_links_bout ON bout_links(bout_id);

-- Линия закрытия по ежедневным снимкам: последний снимок каждого букмекера
-- строго до начала турнира (время начала карда из ESPN, иначе полночь UTC даты турнира)
CREATE TABLE IF NOT EXISTS bout_closing_odds (
    bout_id      TEXT NOT NULL,
    bookmaker    TEXT NOT NULL,
    captured_at  TEXT NOT NULL,         -- время снимка, из которого взята линия
    odds_red     INTEGER,               -- американский формат
    odds_blue    INTEGER,
    PRIMARY KEY (bout_id, bookmaker)
);

-- ---------------------------------------------------------------------------
-- Сопоставление имен из разных источников с id бойца
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fighter_aliases (
    source      TEXT NOT NULL,          -- 'ufcstats_bout', 'espn', 'bestfightodds'
    name        TEXT NOT NULL,
    fighter_id  TEXT REFERENCES fighters(id),  -- NULL = не удалось сопоставить
    method      TEXT NOT NULL,          -- 'exact', 'normalized', 'manual', 'weight', 'unresolved', ...
    note        TEXT,
    PRIMARY KEY (source, name)
);

-- Журнал запусков пайплайна
CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,                   -- 'ok' / 'error'
    details     TEXT
);
