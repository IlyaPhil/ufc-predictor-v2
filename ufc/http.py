"""
HTTP-клиент с паузами между запросами, повторами и сохранением сырых ответов
"""
import gzip
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from ufc.config import MIN_REQUEST_INTERVAL, RAW_DIR, USER_AGENT


class PoliteSession:
    """
    Обертка над requests.Session: не чаще одного запроса в MIN_REQUEST_INTERVAL
    к одному хосту, повторы с экспоненциальной паузой на 429/5xx и сетевых ошибках
    """
    def __init__(self, min_interval: float = MIN_REQUEST_INTERVAL, retries: int = 3):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.min_interval = min_interval
        self.retries = retries
        self._last_request: dict[str, float] = {}

    def get(self, url: str, **kwargs) -> requests.Response:
        host = urlparse(url).netloc
        for attempt in range(self.retries + 1):
            wait = self._last_request.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request[host] = time.monotonic()
            try:
                resp = self.session.get(url, timeout=30, **kwargs)
                if resp.status_code != 429 and resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
            except requests.ConnectionError:
                if attempt == self.retries:
                    raise
            if attempt < self.retries:
                time.sleep(self.min_interval * 2 ** (attempt + 1))
        resp.raise_for_status()
        return resp


def utc_now() -> str:
    """
    Текущее время UTC в ISO-формате — единый формат временных меток в БД
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def save_raw(source: str, name: str, content: bytes, captured_at: str | None = None) -> Path:
    """
    Сохраняет сырой ответ источника в data/raw/<source>/<дата>/<время>_<name>.gz
    """
    ts = datetime.fromisoformat(captured_at) if captured_at else datetime.now(timezone.utc)
    path = RAW_DIR / source / ts.strftime("%Y-%m-%d") / f"{ts.strftime('%H%M%S')}_{name}.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(content)
    return path
