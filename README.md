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

Неоднозначные имена бойцов разрешаются вручную в
[data/manual/fighter_aliases.csv](data/manual/fighter_aliases.csv) — с указанием, на чем основано решение.
