"""Точка входа: Telegram-бот (webhook или polling) + GSI-сервер на порту 30000.

Запуск:  python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from . import handlers
from .config import load_settings
from .gsi_server import GsiService
from .storage import Database

log = logging.getLogger("cs2bot")

COMMANDS = [
    BotCommand(command="stat", description="Отчёт по последнему матчу"),
    BotCommand(command="rounds", description="Разбивка по раундам"),
    BotCommand(command="history", description="Последние матчи"),
    BotCommand(command="cfg", description="cfg-файл для CS2"),
    BotCommand(command="export", description="Выгрузить матч в JSON"),
    BotCommand(command="help", description="Помощь и импорт из консоли"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()

    db = Database(settings.db_path)
    await db.init()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    gsi = GsiService(bot, db, settings)

    dp = Dispatcher()
    dp["db"] = db
    dp["settings"] = settings
    dp["gsi"] = gsi
    dp.include_router(handlers.router)

    await gsi.start()
    await bot.set_my_commands(COMMANDS)

    try:
        if settings.use_webhook:
            app = web.Application()
            SimpleRequestHandler(
                dispatcher=dp, bot=bot, secret_token=settings.webhook_secret
            ).register(app, path=settings.webhook_path)
            setup_application(app, dp, bot=bot)

            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, settings.web_host, settings.web_port).start()
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=True,
            )
            log.info("Webhook: %s (слушаю %s:%s)", settings.webhook_url,
                     settings.web_host, settings.web_port)
            try:
                await asyncio.Event().wait()
            finally:
                await runner.cleanup()
        else:
            log.info("WEBHOOK_BASE_URL не задан — работаю через long polling")
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await gsi.stop()
        await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        if isinstance(e, SystemExit) and e.code not in (None, 0):
            raise


if __name__ == "__main__":
    run()
