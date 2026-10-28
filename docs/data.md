# Данные

БД: `data/ufc.db` (SQLite), схема — [ufc/schema.sql](../ufc/schema.sql).
Состояние на 2026-10-28: 762 турнира, 8695 боев (2001-02-23 … 2026-10-24),
41 352 строки статистики по раундам, 2584 бойца с боями.

## Как загрузить для анализа

Открывайте БД **только на чтение**: ежедневное обновление (22:00 МСК) перезаливает таблицы
истории. Чтение во время обновления безопасно — SQLite отдаст состояние до или после, не середину.

```python
import sqlite3
import pandas as pd

DB = "/home/neuralist/AI/ufc-predictor/data/ufc.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

bouts = pd.read_sql("""
    SELECT b.*, e.date, e.name AS event_name, r.*
    FROM bouts b
    JOIN events e ON e.id = b.event_id
    JOIN bout_results r ON r.bout_id = b.id
""", conn, parse_dates=["date"])
```

Результаты своих расчетов храните вне `ufc.db` (parquet/CSV в своей папке, например `notebooks/`).

## Таблицы

### История (источник — ufcstats; перезаливается ежедневно)

**`events`** — турниры. `id` (id ufcstats), `name`, `date` (YYYY-MM-DD), `location`.

**`fighters`** — профили. `id` — id ufcstats или `unk:<имя>` для заглушек (`is_placeholder = 1`).
`height_cm`, `reach_cm`, `stance`, `dob`, `weight_lbs`, `nickname`.
- `weight_lbs` — **текущий** вес, не на дату боя. Для признаков используйте `bouts.weight_class`.
- Содержит и бойцов, выступавших только до 2001 года (без боев в БД).
- Пропуски у 2584 бойцов с боями: размах рук — 554, стойка — 108, рост — 90, дата рождения — 42.
- `stance`: Orthodox, Southpaw, Switch, редко Open Stance / Sideways.

**`bouts`** — бои, **только то, что известно до боя**.
- `red_id`, `blue_id` → `fighters.id`; `red_name`, `blue_name` — имена как в протоколе боя.
- `weight_class` — нормализованная категория (`Lightweight`, `Women's Flyweight`, `Catch Weight`, …),
  `weight_class_raw` — как в источнике.
- `is_title`, `is_tournament` (финалы TUF, Road to UFC).
- `rounds_scheduled` (3 или 5), `round_minutes` (`'5-5-5'`).

**`bout_results`** — **целевые переменные**, известны только после боя. Ключ `bout_id`.
- `winner`: `red` / `blue` / `draw` / `nc`. С 2001: red 5372, blue 3173, draw 61, nc 89.
- `method_group`: `DEC` 4096, `KO/TKO` 2847, `SUB` 1639, `OTHER` 90 (Overturned, Could Not Continue), `DQ` 23.
  `method` — исходное значение (`Decision - Split`, `TKO - Doctor's Stoppage`, …), `method_details` — детали.
- `end_round`, `end_time_sec` (время внутри последнего раунда), `total_time_sec` (длительность всего боя), `referee`.

**`bout_round_stats`** — статистика бойца в раунде. Ключ (`bout_id`, `corner`, `round`).
- `fighter_id`, `corner` (`red`/`blue`).
- Суффикс `_l` — нанесено (landed), `_a` — выброшено (attempted): `sig_str` (значимые удары),
  `total_str` (все удары), `td` (тейкдауны); по цели — `head`, `body`, `leg`;
  по позиции — `distance`, `clinch`, `ground`.
- `kd` (нокдауны), `sub_att` (попытки сабмишна), `rev` (реверсалы), `ctrl_sec` (время контроля, общее — отдельно «контроля в партере» нет).
- Статистика есть для всех боев с 2001 года. Защиту бойца считают по строкам соперника в тех же боях.

### Снимки (дописываются; источники — ESPN и bestfightodds)

**`card_snapshots`** — снимки кардов, ключ (`captured_at`, `espn_bout_id`).
- `fighter_a`/`fighter_b` — порядок ESPN, **не угол**. `record_a`/`record_b` — полный проф. рекорд
  W-L-D (с боями вне UFC; для истории его нет — в признаки не идет).
- `card_position` 0 = главный бой. `status` — `STATUS_SCHEDULED` и др.
- Вид `upcoming_bouts` — последний снимок каждого турнира.
- Замены и отмены видны как разница между снимками.

**`odds_snapshots`** — коэффициенты, ключ (`captured_at`, `source`, `matchup_id`, `bookmaker`).
- `odds_a`/`odds_b` — американский формат: `-245` (фаворит: ставка 245 → выигрыш 100), `+233` (андердог: 100 → 233).
- Букмекеры: FanDuel, Caesars, BetRivers, BetWay, Unibet, BetMGM, DraftKings и рынки предсказаний Polymarket, Kalshi.
- Снимки начинаются с 2026-10-26.

### Связь снимков с боями (производные, пересчитываются при каждом `daily`)

**`bout_links`** — какой бой ufcstats соответствует бою из источника. Ключ (`source`, `external_id`):
`espn` + `espn_bout_id` или `bestfightodds` + `matchup_id` → `bout_id`.
`a_is_red = 1`, если `fighter_a` источника — красный угол. Связь появляется после того, как
турнир попал в ufcstats (на следующий день); отмененные бои не связываются никогда.
Сопоставление — по неупорядоченной паре бойцов и окну дат (ESPN: дата турнира ±1 день;
bestfightodds: от первого снимка −1 день до последнего +14 дней), только при единственном кандидате.

**`bout_closing_odds`** — линия закрытия по снимкам: для каждого боя и букмекера —
последний снимок строго до начала турнира (`captured_at`), уже в разрезе `odds_red` / `odds_blue`.
Начало турнира — время начала карда из ESPN (для главного боя отсечка на несколько часов раньше
реального закрытия, зато без live-ставок), без ESPN — полночь UTC даты турнира.
Консенсус и вероятность без маржи считаются уже в анализе (формула ниже).

### Служебные

**`fighter_aliases`** — как имя из источника (`ufcstats_bout`, `espn`, `bestfightodds`) сопоставлено с бойцом:
`fighter_id` (NULL = не удалось), `method` (`normalized`, `token_subset`, `manual`, `exact+age`, `ambiguous`, `unresolved`…), `note`.
**`sync_runs`** — журнал запусков пайплайна.

## Подводные камни

1. **Утечка из будущего.** Признаки боя — только из боев с `date < дата_боя`. Проверяйте функцию
   на конкретном примере (ниже) и на `<` vs `<=`.
2. **Средние суммами.** Точность = Σ точных / Σ выброшенных, удары в минуту = Σ ударов / Σ минут.
   Среднее процентов по боям дает 30-секундному бою тот же вес, что 25-минутному.
3. **Мало боев — шум.** У 333 бойцов с боями в UFC всего 1 бой, у 402 — 2. Дебютанту признаки
   должны быть пропусками + «число боев = 0», а не нулями. Полезно стягивание к среднему по дивизиону.
4. **Угол.** Красный выигрывает 62.9% боев с явным победителем (ставят фаворита / чемпиона).
5. **Женские категории и Catch Weight** — отдельные дивизионы; `Catch Weight` — разовый вес, не дивизион.
6. **Время в клетке** — `bout_results.total_time_sec`, а не число раундов × 5 минут.
7. **Заглушки:** 7 боев, где боец не сопоставлен с профилем (`red_id`/`blue_id LIKE 'unk:%'`) —
   у таких бойцов нет физических данных, но история боев связана.

## Примеры запросов

**Бои в «длинном» формате — одна строка на бойца в бою** (удобная база для признаков):

```sql
SELECT e.date, b.id AS bout_id, b.weight_class,
       f.fighter_id, f.opponent_id, f.corner,
       CASE WHEN r.winner = f.corner THEN 1
            WHEN r.winner IN ('red', 'blue') THEN 0 END AS won,   -- NULL для ничьих и NC
       r.method_group, r.total_time_sec
FROM bouts b
JOIN events e ON e.id = b.event_id
JOIN bout_results r ON r.bout_id = b.id
JOIN (SELECT id, red_id AS fighter_id, blue_id AS opponent_id, 'red' AS corner FROM bouts
      UNION ALL
      SELECT id, blue_id, red_id, 'blue' FROM bouts) f ON f.id = b.id
ORDER BY f.fighter_id, e.date
```

**Вероятность из коэффициентов без маржи:**

```python
def implied(american: int) -> float:
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)

pa, pb = implied(-108), implied(-118)       # 0.519, 0.541 — в сумме 1.06: маржа 6%
pa, pb = pa / (pa + pb), pb / (pa + pb)     # 0.490, 0.510
```

Консенсус по рынку — медиана таких вероятностей по букмекерам (не среднее самих коэффициентов).

## Эталонный пример: статистика на дату боя

Renato Moicano (`b6452706b373eea1`) перед 6-м боем в UFC (2018-08-04 vs Cub Swanson),
по 5 предыдущим боям (Niinimaki, Tukhugov, Stephens, Ortega, Kattar), 66.5 мин в клетке (3989 с;
255 с боя со Swanson **не** входят):

| Показатель | Расчет | Значение |
|---|---|---|
| Рекорд в UFC / серия | | 4-1 / 1 победа |
| Дней с прошлого боя / возраст | | 119 / 29.2 |
| Значимые удары в минуту | 387 / 66.5 | 5.82 |
| Точность значимых | 387 / 814 | 48% |
| Тейкдауны на 15 мин / точность | 5 / 66.5 · 15; 5 / 9 | 1.13 / 56% |
| Попытки сабмишна на 15 мин | 1 / 66.5 · 15 | 0.23 |
| Контроль, с на 15 мин | 191 / 66.5 · 15 | 43 |
| Пропущено значимых в минуту | 224 / 66.5 | 3.37 |
| Защита от ударов | 1 − 224 / 743 | 70% |
| Защита от тейкдаунов | 1 − 3 / 13 | 77% |

Функция признаков на дату боя должна воспроизводить эти числа.
