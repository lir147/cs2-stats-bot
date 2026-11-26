"""Обработчики команд Telegram (aiogram 3)."""
from __future__ import annotations

import json
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from typing import TYPE_CHECKING

from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.console_import import ImportError_, parse_any
from core.report import rounds_report, short_line, stat_report

from . import texts
from .config import Settings
from .storage import Database

if TYPE_CHECKING:
    from .gsi_server import GsiService

log = logging.getLogger(__name__)
router = Router(name="main")

MAX_IMPORT_BYTES = 10 * 1024 * 1024


def match_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧾 По раундам", callback_data=f"rounds:{match_id}"),
        InlineKeyboardButton(text="📚 История", callback_data="history"),
    ]])


def _parse_id(command: CommandObject | None) -> int | None:
    if command and command.args:
        arg = command.args.strip().lstrip("#")
        if arg.isdigit():
            return int(arg)
    return None


async def _send_cfg(message: Message, db: Database, settings: Settings, token: str | None = None) -> None:
    token = token or await db.get_or_create_token(message.from_user.id)
    cfg = texts.gsi_cfg(settings.gsi_public_url, token)
    await message.answer_document(
        BufferedInputFile(cfg.encode("utf-8"), filename=texts.CFG_FILENAME),
        caption="Положи этот файл в папку <code>game\\csgo\\cfg</code> и перезапусти CS2. "
                "Никому его не передавай — в нём твой личный токен.",
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, settings: Settings) -> None:
    await message.answer(texts.START)
    await _send_cfg(message, db, settings)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("cfg"))
async def cmd_cfg(message: Message, db: Database, settings: Settings) -> None:
    await _send_cfg(message, db, settings)


@router.message(Command("newtoken"))
async def cmd_newtoken(
    message: Message, db: Database, settings: Settings, gsi: "GsiService"
) -> None:
    old = await db.get_or_create_token(message.from_user.id)
    token = await db.regen_token(message.from_user.id)
    gsi.forget_token(old)
    await message.answer("🔑 Токен обновлён. Старый cfg больше не работает — замени файл на новый:")
    await _send_cfg(message, db, settings, token)


@router.message(Command("stat"))
async def cmd_stat(message: Message, command: CommandObject, db: Database) -> None:
    found = await db.get_match(message.from_user.id, _parse_id(command))
    if not found:
        await message.answer(texts.NO_MATCHES)
        return
    match_id, m = found
    await message.answer(stat_report(m), reply_markup=match_keyboard(match_id))


@router.message(Command("rounds"))
async def cmd_rounds(message: Message, command: CommandObject, db: Database) -> None:
    found = await db.get_match(message.from_user.id, _parse_id(command))
    if not found:
        await message.answer(texts.NO_MATCHES)
        return
    await message.answer(rounds_report(found[1]))


async def _history_text(db: Database, tg_id: int) -> str:
    items = await db.list_matches(tg_id, 10)
    if not items:
        return texts.NO_MATCHES
    lines = ["📚 <b>Последние матчи</b>"]
    lines += [f"<code>#{mid}</code> {short_line(m)}" for mid, m in items]
    lines.append("\nОтчёт по матчу: <code>/stat id</code>, раунды: <code>/rounds id</code>")
    return "\n".join(lines)


@router.message(Command("history"))
async def cmd_history(message: Message, db: Database) -> None:
    await message.answer(await _history_text(db, message.from_user.id))


@router.message(Command("export"))
async def cmd_export(message: Message, command: CommandObject, db: Database) -> None:
    found = await db.get_match(message.from_user.id, _parse_id(command))
    if not found:
        await message.answer(texts.NO_MATCHES)
        return
    match_id, m = found
    data = json.dumps(m.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    await message.answer_document(BufferedInputFile(data, filename=f"cs2_match_{match_id}.json"))


@router.callback_query(F.data.startswith("rounds:"))
async def cb_rounds(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    try:
        match_id = int(cb.data.split(":", 1)[1])
    except ValueError:
        return
    found = await db.get_match(cb.from_user.id, match_id)
    if found and cb.message:
        await cb.message.answer(rounds_report(found[1]))


@router.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    if cb.message:
        await cb.message.answer(await _history_text(db, cb.from_user.id))


@router.message(F.document)
async def on_document(message: Message, bot: Bot, db: Database) -> None:
    doc = message.document
    if doc.file_size and doc.file_size > MAX_IMPORT_BYTES:
        await message.answer("Файл больше 10 МБ — пришли лог одного матча.")
        return
    try:
        buf = await bot.download(doc)
        raw = buf.read() if buf else b""
        m = parse_any(raw)
    except ImportError_ as e:
        await message.answer(f"Не получилось импортировать: {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("import failed")
        await message.answer("Не получилось прочитать файл. Пришли лог консоли (condump) или JSON.")
        return
    if not m.player_name and message.from_user:
        m.player_name = message.from_user.full_name
    match_id = await db.save_match(message.from_user.id, m)
    await message.answer(stat_report(m), reply_markup=match_keyboard(match_id))


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Не понял 🤔 Команды — /help")
