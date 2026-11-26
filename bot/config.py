"""Настройки из переменных окружения (и файла .env, если он есть)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    # webhook (если WEBHOOK_BASE_URL пуст — бот работает через long polling)
    webhook_base_url: str
    webhook_path: str
    webhook_secret: str
    web_host: str
    web_port: int
    # приём данных от CS2
    gsi_host: str
    gsi_port: int
    gsi_public_url: str  # адрес, который пропишется в cfg игрока
    gsi_dump_dir: str  # если задан — сырые payload'ы пишутся в JSONL
    db_path: str
    live_round_updates: bool  # слать короткое сообщение после каждого раунда

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_base_url)

    @property
    def webhook_url(self) -> str:
        return self.webhook_base_url.rstrip("/") + self.webhook_path


def load_settings() -> Settings:
    _load_dotenv(Path(os.getenv("ENV_FILE", ".env")))
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    gsi_port = int(os.getenv("GSI_PORT", "30000"))
    return Settings(
        bot_token=token,
        webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").strip(),
        webhook_path=os.getenv("WEBHOOK_PATH", "/tg/webhook"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(32),
        web_host=os.getenv("WEB_HOST", "0.0.0.0"),
        web_port=int(os.getenv("WEB_PORT", "8080")),
        gsi_host=os.getenv("GSI_HOST", "0.0.0.0"),
        gsi_port=gsi_port,
        gsi_public_url=os.getenv("GSI_PUBLIC_URL", f"http://127.0.0.1:{gsi_port}/").strip(),
        gsi_dump_dir=os.getenv("GSI_DUMP_DIR", "").strip(),
        db_path=os.getenv("DB_PATH", "data/bot.sqlite3"),
        live_round_updates=os.getenv("LIVE_ROUND_UPDATES", "0") in ("1", "true", "yes"),
    )
