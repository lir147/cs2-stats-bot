"""Импорт матча не из живого GSI, а из файла.

Поддерживаются три формата:

1. **Лог консоли CS2** (``condump`` или ``con_logfile``) — блоки
   ``Player: Ник - Damage Given`` / ``Damage Given to "X" - 100 in 4 hits``,
   которые игра печатает после каждого раунда. Фраг считается, если по
   игроку нанесено ≥100 урона, смерть — если получено ≥100. Это приближение:
   в консоли нет kill-feed, денег и времени.
2. **Запись GSI** — JSON-массив или JSONL с payload'ами, которые прислал CS2
   (бот умеет писать такие логи, см. ``GSI_DUMP_DIR``). Прогоняется через
   тот же трекер, что и живые данные — точность максимальная.
3. **Экспорт бота** — JSON ``MatchRecord`` (команда /export).
"""
from __future__ import annotations

import json
import re
from typing import Any

from .gsi import GsiTracker
from .models import MatchRecord, RoundRecord

_DMG_RE = re.compile(
    r'Damage\s+(Given to|Taken from)\s+"(?P<name>.+?)"\s*-\s*(?P<dmg>\d+)\s+in\s+(?P<hits>\d+)\s+hits?',
    re.IGNORECASE,
)
_HEADER_RE = re.compile(r"Player:\s*(?P<name>.+?)\s*-\s*Damage\s+(?P<kind>Given|Taken)", re.IGNORECASE)
_MAP_RE = re.compile(r"\b((?:de|cs|ar|dz)_[a-z0-9_]+)\b", re.IGNORECASE)


class ImportError_(ValueError):
    pass


def parse_any(raw: bytes | str) -> MatchRecord:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.lstrip("﻿")
    stripped = text.strip()
    if not stripped:
        raise ImportError_("Файл пустой")

    if stripped[0] in "[{":
        try:
            return _parse_json(stripped)
        except json.JSONDecodeError:
            # возможно JSONL
            lines = [ln for ln in stripped.splitlines() if ln.strip()]
            try:
                payloads = [json.loads(ln) for ln in lines]
            except json.JSONDecodeError as e:
                raise ImportError_(f"Не удалось разобрать JSON: {e}") from e
            return replay_gsi(payloads)

    if "Damage Given" in text or "Damage Taken" in text:
        return parse_console(text)

    raise ImportError_(
        "Не узнаю формат. Пришли лог консоли CS2 (condump), запись GSI (JSON/JSONL) "
        "или файл экспорта бота."
    )


def _parse_json(s: str) -> MatchRecord:
    data: Any = json.loads(s)
    if isinstance(data, list):
        return replay_gsi(data)
    if isinstance(data, dict):
        if "rounds" in data:
            m = MatchRecord.from_dict(data)
            m.source = "json"
            return m
        if "provider" in data:
            return replay_gsi([data])
    raise ImportError_("JSON не похож ни на запись GSI, ни на экспорт бота")


def replay_gsi(payloads: list[dict[str, Any]]) -> MatchRecord:
    tracker = GsiTracker()
    finished: MatchRecord | None = None
    for i, p in enumerate(payloads):
        if not isinstance(p, dict):
            continue
        ts = (p.get("provider") or {}).get("timestamp")
        for ev in tracker.process(p, now=float(ts) if ts else float(i)):
            if ev.kind == "match_finished":
                finished = ev.match
    for ev in tracker.flush():
        if ev.kind == "match_finished":
            finished = ev.match
    if finished is None and tracker.match is not None:
        # запись оборвалась до конца матча — закрываем то, что есть
        if tracker.cur is not None:
            tracker._finalize_round(None)  # noqa: SLF001
        finished = tracker.match
    if finished is None or not finished.rounds:
        raise ImportError_("В записи GSI не нашлось ни одного сыгранного раунда")
    finished.source = "gsi-file"
    return finished


def parse_console(text: str) -> MatchRecord:
    m = MatchRecord(source="console")
    mm = _MAP_RE.search(text)
    if mm:
        m.map_name = mm.group(1).lower()

    blocks: list[dict[str, dict[str, int]]] = []
    current: dict[str, dict[str, int]] | None = None
    section = "given"
    player_name = ""

    for line in text.splitlines():
        h = _HEADER_RE.search(line)
        if h:
            player_name = player_name or h.group("name").strip()
            kind = h.group("kind").lower()
            if kind == "given":
                current = {"given": {}, "taken": {}}
                blocks.append(current)
            elif current is None:
                current = {"given": {}, "taken": {}}
                blocks.append(current)
            section = kind
            continue
        d = _DMG_RE.search(line)
        if not d:
            continue
        if current is None:
            current = {"given": {}, "taken": {}}
            blocks.append(current)
        kind = "given" if d.group(1).lower().startswith("given") else "taken"
        section = kind
        name = d.group("name")
        current[section][name] = current[section].get(name, 0) + int(d.group("dmg"))

    if not blocks:
        raise ImportError_("В логе консоли не найдено ни одного блока Damage Given/Taken")

    m.player_name = player_name
    for i, b in enumerate(blocks, start=1):
        given = b["given"]
        taken = b["taken"]
        kills = sum(1 for v in given.values() if v >= 100)
        died = sum(taken.values()) >= 100
        m.rounds.append(RoundRecord(
            number=i,
            kills=kills,
            # урон сверх 100 по одному игроку в ADR не засчитывается
            damage=sum(min(v, 100) for v in given.values()),
            died=died,
            survived=not died,
        ))
    return m
