"""
Пути и общие настройки проекта
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
# Сырые ответы источников (HTML/JSON/CSV) — позволяют перепарсить историю без повторных запросов
RAW_DIR = DATA_DIR / "raw"
# Ручные справочники (алиасы имён и т.п.) — единственная часть data/, которая лежит в git
MANUAL_DIR = DATA_DIR / "manual"
DB_PATH = DATA_DIR / "ufc.db"
# Снимки карда и коэффициентов (JSONL). В облаке указывает на checkout репозитория с данными
SNAPSHOT_DIR = Path(os.environ.get("UFC_SNAPSHOT_DIR", DATA_DIR / "snapshots"))

USER_AGENT = "Mozilla/5.0 (compatible; ufc-predictor/0.1; personal non-commercial research)"
# Минимальная пауза между запросами к одному хосту, сек.
MIN_REQUEST_INTERVAL = 3.0

# Зеркало ufcstats.com: сам сайт закрыт JS-проверкой от ботов,
# а этот репозиторий ежедневно публикует выгрузку в CSV
UFCSTATS_MIRROR_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main"
# Турниры раньше этой даты не загружаем: ранний UFC шел по другим правилам и без статистики.
# Обучающую выборку стоит начинать позже, чтобы у бойцов успела накопиться история
HISTORY_START = "2001-01-01"
