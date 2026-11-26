"""HTTP-сервер, принимающий Game State Integration от CS2 (порт 30000)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from aiogram import Bot

from core.gsi import GsiEvent, GsiTracker
from core.report import stat_report

from .config import Settings
from .handlers import match_keyboard
from .storage import Database

log = logging.getLogger(__name__)

FLUSH_AFTER_SEC = 15  # gameover пришёл, а «свой» payload — нет
IDLE_DROP_SEC = 3 * 3600  # забываем трекеры, по которым давно нет данных


class GsiService:
    def __init__(self, bot: Bot, db: Database, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.trackers: dict[str, GsiTracker] = {}
        self.last_seen: dict[str, float] = {}  # token → time.monotonic()
        self.token_cache: dict[str, int] = {}
        self._runner: web.AppRunner | None = None
        self._flusher: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ server
    def make_app(self) -> web.Application:
        app = web.Application(client_max_size=2 * 1024 * 1024)
        app.router.add_post("/", self.handle)
        app.router.add_post("/gsi", self.handle)
        app.router.add_get("/health", self.health)
        return app

    async def start(self) -> None:
        self._runner = web.AppRunner(self.make_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.settings.gsi_host, self.settings.gsi_port)
        await site.start()
        self._flusher = asyncio.create_task(self._flush_loop())
        log.info("GSI слушает %s:%s", self.settings.gsi_host, self.settings.gsi_port)

    async def stop(self) -> None:
        if self._flusher:
            self._flusher.cancel()
        if self._runner:
            await self._runner.cleanup()

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "active": len(self.trackers)})

    async def handle(self, request: web.Request) -> web.Response:
        try:
            payload: dict[str, Any] = await request.json(loads=json.loads)
        except Exception:  # noqa: BLE001
            return web.Response(status=400, text="bad json")
        if not isinstance(payload, dict):
            return web.Response(status=400, text="bad json")
        token = str((payload.get("auth") or {}).get("token", ""))
        tg_id = await self._resolve(token)
        if tg_id is None:
            return web.Response(status=403, text="unknown token")

        self.last_seen[token] = time.monotonic()
        self._dump(token, payload)
        tracker = self.trackers.setdefault(token, GsiTracker())
        try:
            events = tracker.process(payload)
        except Exception:  # noqa: BLE001
            log.exception("ошибка разбора GSI")
            return web.Response(text="ok")
        if events:
            task = asyncio.create_task(self._handle_events(tg_id, events))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return web.Response(text="ok")

    async def _resolve(self, token: str) -> int | None:
        if not token:
            return None
        if token in self.token_cache:
            return self.token_cache[token]
        tg_id = await self.db.user_by_token(token)
        if tg_id is not None:
            self.token_cache[token] = tg_id
        return tg_id

    def forget_token(self, token: str) -> None:
        self.token_cache.pop(token, None)
        self.trackers.pop(token, None)

    # ------------------------------------------------------------ events
    async def _handle_events(self, tg_id: int, events: list[GsiEvent]) -> None:
        for ev in events:
            try:
                if ev.kind == "match_finished":
                    match_id = await self.db.save_match(tg_id, ev.match)
                    await self.bot.send_message(
                        tg_id, "🏁 Матч завершён!\n\n" + stat_report(ev.match),
                        reply_markup=match_keyboard(match_id),
                    )
                elif ev.kind == "round_finished" and self.settings.live_round_updates and ev.round:
                    r = ev.round
                    res = "✅" if r.won else "❌" if r.won is False else "▫️"
                    await self.bot.send_message(
                        tg_id,
                        f"{res} Раунд {r.number}: {r.kills} фраг., {r.damage} урона"
                        + (", погиб" if r.died else ", выжил"),
                        disable_notification=True,
                    )
            except Exception:  # noqa: BLE001
                log.exception("не удалось обработать событие %s", ev.kind)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for token, tracker in list(self.trackers.items()):
                idle = now - self.last_seen.get(token, now)
                if tracker.pending_finish is not None and idle >= FLUSH_AFTER_SEC:
                    events = tracker.flush()
                    tg_id = self.token_cache.get(token)
                    if events and tg_id is not None:
                        await self._handle_events(tg_id, events)
                elif idle >= IDLE_DROP_SEC:
                    self.trackers.pop(token, None)
                    self.last_seen.pop(token, None)

    # -------------------------------------------------------------- dump
    def _dump(self, token: str, payload: dict[str, Any]) -> None:
        if not self.settings.gsi_dump_dir:
            return
        try:
            d = Path(self.settings.gsi_dump_dir)
            d.mkdir(parents=True, exist_ok=True)
            name = f"{token[:8]}_{datetime.now():%Y%m%d}.jsonl"
            with open(d / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("не удалось записать дамп GSI")
