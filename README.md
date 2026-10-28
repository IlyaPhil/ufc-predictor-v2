# UFC predictor v2

Пайплайн сбора данных о боях UFC для обучения моделей предсказания исходов.

## Источники

| Данные | Источник | Обновление |
|---|---|---|
| История: турниры, бои, результаты, статистика по раундам, профили бойцов | [ufcstats.com](http://ufcstats.com) через CSV-зеркало [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) | полная перезаливка, бои с 2001 года |
| Карды предстоящих турниров | публичный API ESPN | снимки, только дописываются |
| Коэффициенты (moneyline) | [bestfightodds.com](https://www.bestfightodds.com) | снимки, только дописываются |

Признаки для моделей в БД не хранятся: они вычисляются из сырых данных на дату боя,
одинаково для обучения и для прогноза. Схема БД — [ufc/schema.sql](ufc/schema.sql).

Документация: [архитектура и схема](docs/architecture.md) · [словарь данных и загрузка в pandas](docs/data.md) ·
[принятые решения](docs/decisions.md) · [контекст для AI-агента](CLAUDE.md).

## Команды

```bash
python -m ufc.cli sync-history     # история из зеркала ufcstats + проверки качества
python -m ufc.cli collect          # снимки карда и коэффициентов в data/snapshots/ (БД не нужна)
python -m ufc.cli load-snapshots   # загрузить снимки в БД (повторно — без дублей)
python -m ufc.cli daily            # все три шага подряд
python -m ufc.cli check            # подробный отчет проверок качества
python -m ufc.cli status           # сводка по БД
python -m ufc.cli unresolved       # имена, не сопоставленные с бойцами
```

## Расписание

- **Облако** ([.github/workflows/collect.yml](.github/workflows/collect.yml)): GitHub Actions ежедневно
  (в субботу — каждые 3 часа) запускает `collect` и коммитит снимки в ветку `snapshots`.
- **Локально**: ветка `snapshots` подключена как `data/snapshots`
  (`git worktree add data/snapshots snapshots`), а systemd-таймер ежедневно в 22:00
  (или после включения ПК, если время пропущено) запускает `daily`: история + `git pull` снимков + загрузка в БД.
  Лог — `logs/daily.log`.

```bash
cp deploy/systemd/ufc-daily.* ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now ufc-daily.timer    # включить
systemctl --user list-timers ufc-daily.timer     # когда следующий запуск
systemctl --user disable --now ufc-daily.timer   # отключить
```

Неоднозначные имена бойцов разрешаются вручную в
[data/manual/fighter_aliases.csv](data/manual/fighter_aliases.csv) — с указанием, на чем основано решение.
